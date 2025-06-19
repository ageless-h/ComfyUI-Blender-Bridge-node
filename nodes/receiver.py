# nodes/receiver.py

import zmq
import threading
import msgspec
import time
import os
import folder_paths
import json
import urllib.request
import urllib.error
import uuid
import asyncio
import websockets

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

def find_and_update_load_image_node(workflow, image_filename):
    """
    在工作流中查找'LoadImage'节点并更新其'image'参数。
    这是一个简化的方法。更强大的解决方案可能需要Blender提供更多元数据来识别正确的节点。
    """
    found = False
    for node_id, node_data in workflow.items():
        if node_data.get("class_type") == "LoadImage":
            node_data["inputs"]["image"] = image_filename
            print(f"[BlenderBridge] Updated LoadImage node '{node_id}' with image '{image_filename}'")
            found = True
            # 暂时，我们假设只更新找到的第一个。
            # 更高级的实现可以更新多个或使用元数据来定位特定节点。
            break 
    if not found:
        print("[BlenderBridge] 警告: 在提供的工作流中未找到 'LoadImage' 节点。")
    return workflow

async def track_workflow_and_cleanup(prompt_id, files_to_delete):
    """
    通过 WebSocket 连接到 ComfyUI，监视工作流执行，
    并在完成后清理临时文件。
    """
    client_id = str(uuid.uuid4())
    ws_uri = f"ws://127.0.0.1:8188/ws?clientId={client_id}"
    
    try:
        async with websockets.connect(ws_uri) as websocket:
            print(f"[BlenderBridge-Cleanup] 正在监视 Prompt ID: {prompt_id}")
            while True:
                message_data = await websocket.recv()
                if isinstance(message_data, str):
                    message = json.loads(message_data)
                    # 检查 'executed' 类型的消息
                    if message.get('type') == 'executed':
                        data = message.get('data', {})
                        if data.get('prompt_id') == prompt_id:
                            print(f"[BlenderBridge-Cleanup] Prompt ID: {prompt_id} 执行完毕。开始清理文件。")
                            for file_path in files_to_delete:
                                try:
                                    os.remove(file_path)
                                    print(f"[BlenderBridge-Cleanup] 已删除: {os.path.basename(file_path)}")
                                except OSError as e:
                                    print(f"[BlenderBridge-Cleanup] 清理文件时出错 {file_path}: {e}")
                            # 清理完成后退出循环
                            break
    except Exception as e:
        print(f"[BlenderBridge-Cleanup] WebSocket 错误: {e}")
        return None

def run_async_in_thread(loop, coro):
    """在一个新线程中运行 async coroutine。"""
    def worker(loop, coro):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
        loop.close()
    
    thread = threading.Thread(target=worker, args=(loop, coro), daemon=True)
    thread.start()
    return thread

def queue_prompt(prompt_workflow, files_to_delete):
    """
    使用 ComfyUI 的 HTTP API 将工作流提交到队列，
    并启动一个清理线程。
    """
    client_id = str(uuid.uuid4())
    payload = {"prompt": prompt_workflow, "client_id": client_id}
    data = json.dumps(payload).encode('utf-8')

    req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=data)
    
    try:
        print("[BlenderBridge] 正在向 ComfyUI API 提交工作流...")
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("[BlenderBridge] 工作流已成功提交。")
                response_data = json.loads(response.read())
                prompt_id = response_data.get('prompt_id')
                
                if prompt_id and files_to_delete:
                    # 在新线程中启动异步清理任务
                    new_loop = asyncio.new_event_loop()
                    run_async_in_thread(new_loop, track_workflow_and_cleanup(prompt_id, files_to_delete))
                
                return response_data
            else:
                error_body = response.read().decode('utf-8', errors='ignore')
                print(f"[BlenderBridge] API 错误: {response.status} {response.reason}\n详情: {error_body}")
                return None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        print(f"[BlenderBridge] HTTP 错误: {e.code} {e.reason}\n详情: {error_body}")
        return None
    except Exception as e:
        print(f"[BlenderBridge] 无法连接到 ComfyUI API: {e}")
        return None

def zmq_server_worker():
    """
    在后台线程中运行，使用多部分消息协议监听来自 Blender 的请求，并根据请求类型进行路由。
    """
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    address = "tcp://127.0.0.1:5555"
    print(f"[BlenderBridge] 正在启动 ZeroMQ 服务器，绑定到 {address}...")
    socket.bind(address)
    
    decoder = msgspec.msgpack.Decoder()
    encoder = msgspec.msgpack.Encoder()

    while True:
        try:
            # 阻塞并等待来自 Blender 的多部分消息
            parts = socket.recv_multipart()
            
            # 第一部分是元数据
            metadata = decoder.decode(parts[0])
            print(f"[BlenderBridge] 收到请求, 元数据: {metadata.get('type', 'N/A')}")

            request_type = metadata.get("type")

            # --- 请求路由 ---
            if request_type == "ping":
                # 1. 处理 Ping 请求 (握手)
                print("[BlenderBridge] 收到 Ping 请求，正在回复 Pong...")
                reply = {"status": "ok", "message": "pong"}
                socket.send(encoder.encode(reply))
                continue

            # 检查是全自动模式还是交互模式
            is_auto_mode = "workflow" in metadata

            if is_auto_mode:
                # --- 全自动模式 ---
                print("[BlenderBridge] 检测到全自动模式 (包含工作流)。")
                workflow = metadata["workflow"]
                
                if len(parts) < 2:
                    raise ValueError("自动模式请求需要元数据和至少一个图像数据部分。")
                
                # 第二部分是图像数据
                image_data = parts[1]
                original_filename = sanitize_filename(metadata.get("filename", "image.png"))
                
                # 将图像保存到 ComfyUI 的 input 目录
                input_dir = folder_paths.get_input_directory()
                # 使用子目录以保持整洁
                subfolder = "blender_bridge_input"
                relative_filename = os.path.join(subfolder, f"{uuid.uuid4()}_{original_filename}")
                full_path = os.path.join(input_dir, relative_filename)
                
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(image_data)
                
                print(f"[BlenderBridge] 图像已保存到: {full_path}")

                # 修改工作流以使用新保存的图像
                modified_workflow = find_and_update_load_image_node(workflow, relative_filename)
                
                # 提交工作流并安排临时文件清理
                queue_prompt(modified_workflow, [full_path])

                reply = {"status": "ok", "message": "Workflow received and queued for execution."}
                socket.send(encoder.encode(reply))

            else:
                # --- 交互模式 ---
                print("[BlenderBridge] 检测到交互模式 (无工作流)。")
                
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
            print(f"[BlenderBridge] 服务器线程出错: {e}")
            try:
                reply = {"status": "error", "message": str(e)}
                socket.send(encoder.encode(reply))
            except Exception as send_e:
                print(f"[BlenderBridge] 无法发送错误回复: {send_e}")
            time.sleep(1)

def start_server_thread():
    """如果后台服务器线程尚未运行，则初始化并启动它。"""
    global SERVER_THREAD
    if SERVER_THREAD is None or not SERVER_THREAD.is_alive():
        SERVER_THREAD = threading.Thread(target=zmq_server_worker, daemon=True)
        SERVER_THREAD.start()

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