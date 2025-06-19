# nodes/receiver.py

import zmq
import threading
import msgspec
import time
import os
import folder_paths
import uuid

# --- 全局状态 (交互模式) ---
# 这个字典将为"交互模式"保存从 Blender 接收到的最新数据。
# 它充当服务器线程和节点执行之间的共享空间。
LATEST_DATA = {
    "lock": threading.Lock(),
    "files": [],
    "metadata": None,
    "return_info": None,
}

# 一个 threading.Event，用于在新的交互式数据到达时发出信号。
# 节点的 execute 方法将等待此事件。
NEW_DATA_EVENT = threading.Event()

# 服务器线程的全局引用，以确保只有一个正在运行。
SERVER_THREAD = None

def get_comfy_temp_directory():
    """返回 ComfyUI 临时目录，并确保它存在。"""
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir

def sanitize_filename(filename):
    """
    通过删除目录遍历字符来清理文件名。
    这是一种安全措施，可防止在预期目录之外写入文件。
    """
    return os.path.basename(str(filename))

def zmq_server_worker():
    """
    在后台线程中运行，使用多部分消息协议监听来自 Blender 的请求。
    """
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    address = "tcp://127.0.0.1:5555"
    print(f"[BlenderBridge] 正在启动 ZeroMQ 服务器，绑定到 {address}...")
    socket.bind(address)
    
    decoder = msgspec.msgpack.Decoder()
    encoder = msgspec.msgpack.Encoder()

    try:
        while True:
            try:
                # 阻塞并等待来自 Blender 的多部分消息
                parts = socket.recv_multipart()
                
                # 第一部分是元数据
                metadata = decoder.decode(parts[0])
                print(f"[BlenderBridge] 收到请求, 元数据: {metadata.get('type', 'N/A')}")

                request_type = metadata.get("type")

                # 1. 处理 Ping 请求 (握手)
                if request_type == "ping":
                    print("[BlenderBridge] 收到 Ping 请求，正在回复 Pong...")
                    reply = {"status": "ok", "message": "pong"}
                    socket.send(encoder.encode(reply))
                    continue

                # --- 交互模式 ---
                # 所有非 ping 请求都被视为交互式数据。
                print("[BlenderBridge] 检测到交互模式数据。")
                
                if len(parts) < 2:
                    raise ValueError("交互模式请求需要元数据和图像数据部分。")
                
                # 第二部分是图像数据
                image_data = parts[1]
                original_filename = sanitize_filename(metadata.get("filename", "image.png"))
                
                # 将图像保存到 ComfyUI 的临时目录
                temp_dir = get_comfy_temp_directory()
                temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{original_filename}")
                
                with open(temp_path, "wb") as f:
                    f.write(image_data)
                
                print(f"[BlenderBridge] 交互式图像已保存到临时文件: {temp_path}")
                
                # 准备要传递给 DataHub 节点的数据
                file_info = {
                    "path": temp_path,
                    "type": metadata.get("render_type", "render"), # 'multilayer_exr', 'image', etc.
                    "original_name": original_filename,
                }

                # 更新全局状态，以便 Receiver 节点可以获取
                with LATEST_DATA["lock"]:
                    LATEST_DATA["files"] = [file_info]
                    LATEST_DATA["metadata"] = metadata
                    LATEST_DATA["return_info"] = metadata.get("return_info")

                # 发出新数据可用的信号
                NEW_DATA_EVENT.set()
                
                reply = {"status": "ok", "message": "Interactive data received."}
                socket.send(encoder.encode(reply))

            except Exception as e:
                print(f"[BlenderBridge] 服务器在处理请求时遇到错误: {e}")
                # 即使有错误，也尝试向Blender发送一个回复，以防止其卡在等待状态
                try:
                    error_reply = {"status": "error", "message": str(e)}
                    socket.send(encoder.encode(error_reply))
                except Exception as send_e:
                    print(f"[BlenderBridge] 无法发送错误回复给 Blender: {send_e}")
    finally:
        print("[BlenderBridge] 正在关闭 ZeroMQ 服务器...")
        socket.close()
        context.term()

def start_server_thread():
    """启动 ZMQ 服务器线程（如果尚未运行）。"""
    global SERVER_THREAD
    if SERVER_THREAD is None or not SERVER_THREAD.is_alive():
        SERVER_THREAD = threading.Thread(target=zmq_server_worker, daemon=True)
        SERVER_THREAD.start()
        print("[BlenderBridge] ZeroMQ 服务器线程已启动。")
    return SERVER_THREAD

class BlenderBridge_Receiver:
    _server_started = False

    def __init__(self):
        if not BlenderBridge_Receiver._server_started:
            start_server_thread()
            BlenderBridge_Receiver._server_started = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("BRIDGE_PIPE",)
    RETURN_NAMES = ("bridge_pipe",)
    FUNCTION = "execute"
    CATEGORY = "Blender Bridge"
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 等待来自 ZMQ 线程的事件
        is_new_data = NEW_DATA_EVENT.wait(timeout=0.01)
        if is_new_data:
            return float("NaN") # 返回 NaN 强制 ComfyUI 重新运行此节点
        return 0

    def execute(self):
        print("[BlenderBridge-Receiver] 等待来自 Blender 的交互式数据...")
        
        # 阻塞直到接收到新数据
        NEW_DATA_EVENT.wait()
        
        with LATEST_DATA["lock"]:
            # 复制数据以避免竞争条件
            pipe_data = {
                "files": list(LATEST_DATA["files"]),
                "metadata": dict(LATEST_DATA["metadata"]),
                "return_info": dict(LATEST_DATA["return_info"]) if LATEST_DATA["return_info"] else None,
            }
            # 消费事件，以便下次可以再次等待
            NEW_DATA_EVENT.clear()
        
        print("[BlenderBridge-Receiver] 已收到交互式数据。将管道传递到下游。")
        return (pipe_data,) 