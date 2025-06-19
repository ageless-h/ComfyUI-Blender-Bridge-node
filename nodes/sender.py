# nodes/sender.py
import torch
import folder_paths
import os
from PIL import Image
import numpy as np
import json
import urllib.request
import urllib.error

def tensor_to_pil(tensor):
    """将输入的 PyTorch 张量转换为 PIL.Image 对象。"""
    # 移除批处理维度, 将数据范围从 [0, 1] 转换为 [0, 255]
    image_np = tensor.squeeze(0).mul(255).clamp(0, 255).byte().cpu().numpy()
    return Image.fromarray(image_np, 'RGB')

class BlenderBridge_Sender:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "bridge_pipe": ("BRIDGE_PIPE",),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "execute"
    CATEGORY = "Blender Bridge"
    OUTPUT_NODE = True # 这是一个终点节点

    def execute(self, image, bridge_pipe):
        if not bridge_pipe or "return_info" not in bridge_pipe or not bridge_pipe["return_info"]:
            print("[BlenderBridge-Sender] 警告: bridge_pipe 中缺少 return_info，跳过发送。")
            return {}

        return_info = bridge_pipe["return_info"]
        server_address = return_info.get("blender_server_address")
        image_name = return_info.get("image_datablock_name")

        if not server_address or not image_name:
            print("[BlenderBridge-Sender] 警告: return_info 中缺少必要信息，跳过发送。")
            return {}
        
        # 将张量图像转换为 PIL Image
        pil_image = tensor_to_pil(image)

        # 准备 HTTP 请求
        headers = {
            "X-Blender-Image-Name": image_name
        }
        
        # 智能模式判断
        is_local = "127.0.0.1" in server_address or "localhost" in server_address

        try:
            if is_local:
                # --- 本地高速模式 ---
                # 将图像保存到 ComfyUI 的输出目录
                file_path, _ = self.save_image_local(pil_image, "blender_bridge_output")
                print(f"[BlenderBridge-Sender] 本地模式: 图像已保存至 {file_path}")

                # 准备 JSON 载荷
                payload = json.dumps({"image_path": file_path}).encode('utf-8')
                headers["Content-Type"] = "application/json"
                req = urllib.request.Request(f"{server_address}/update_image", data=payload, headers=headers)
                print(f"[BlenderBridge-Sender] 正在向 Blender 发送路径: {file_path}")
            else:
                # --- 远程兼容模式 ---
                # 在内存中将图像编码为 PNG
                import io
                buffer = io.BytesIO()
                pil_image.save(buffer, format="PNG")
                payload = buffer.getvalue()
                
                headers["Content-Type"] = "image/png"
                req = urllib.request.Request(f"{server_address}/update_image", data=payload, headers=headers)
                print(f"[BlenderBridge-Sender] 远程模式: 正在向 Blender 发送图像数据...")

            # 发送请求
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    print(f"[BlenderBridge-Sender] 成功将图像 '{image_name}' 发送回 Blender。")
                else:
                    error_body = response.read().decode('utf-8', errors='ignore')
                    print(f"[BlenderBridge-Sender] 发送失败: {response.status} {response.reason}\n详情: {error_body}")

        except Exception as e:
            print(f"[BlenderBridge-Sender] 发送图像回 Blender 时出错: {e}")

        return {}

    def save_image_local(self, image_pil, filename_prefix):
        """将 PIL 图像保存到输出目录，并返回完整路径。"""
        # 使用 ComfyUI 的内建方法来获取带编号的文件名
        full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, self.output_dir)
        file_path = os.path.join(full_output_folder, f"{filename}_{counter:05}.png")
        image_pil.save(file_path)
        return file_path, f"{filename}_{counter:05}.png" 