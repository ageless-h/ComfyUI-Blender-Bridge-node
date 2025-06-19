# nodes/hub.py
import torch
import numpy as np
from PIL import Image
import os
import json
import struct
import folder_paths

# --- 可选依赖项：Cryptomatte 和 EXR 通道支持 ---
# 尝试导入 OpenEXR 和 Imath。如果它们不可用，
# EXR 相关功能将被禁用，并会打印一条警告。
try:
    import OpenEXR
    import Imath
    OPENEXR_SUPPORT = True
except ImportError:
    print("[BlenderBridge-DataHub] 警告: 未安装 OpenEXR-python。将禁用 EXR 多通道和 Cryptomatte 功能。")
    print("[BlenderBridge-DataHub] 请在您的 ComfyUI Python 环境中运行: pip install OpenEXR-python")
    OPENEXR_SUPPORT = False

def pil_to_tensor(image_pil):
    """将 PIL.Image 对象转换为 ComfyUI 所需的 PyTorch 张量格式。"""
    if image_pil.mode == 'RGBA':
        image_pil = image_pil.convert('RGB')
    image_np = np.array(image_pil).astype(np.float32) / 255.0
    # 添加批处理维度
    tensor = torch.from_numpy(image_np)[None,]
    return tensor

def handle_standard_image(file_path):
    """从文件路径加载标准图像 (PNG, JPG) 并转换为张量。"""
    if not os.path.exists(file_path):
        print(f"[BlenderBridge-DataHub] 警告: 在 {file_path} 找不到文件")
        return None
    try:
        img_pil = Image.open(file_path)
        return pil_to_tensor(img_pil)
    except Exception as e:
        print(f"[BlenderBridge-DataHub] 加载标准图像 {file_path} 时出错: {e}")
        return None

def get_crypto_manifest_and_prefix(header):
    """从 EXR 标头动态查找 Cryptomatte 清单和层前缀。"""
    for key, value in header.items():
        if key.endswith('.manifest'):
            prefix = key[:-len('manifest')]
            manifest = json.loads(value)
            return manifest, prefix
    return None, None

def hex_to_float(hex_str):
    """将十六进制哈希字符串转换为浮点 ID。"""
    return struct.unpack('!f', bytes.fromhex(hex_str))[0]

def extract_cryptomatte_mask(exr_file, header, width, height, object_name):
    """从已打开的 EXR 文件中提取 Cryptomatte 遮罩。"""
    manifest, prefix = get_crypto_manifest_and_prefix(header)
    if not manifest:
        print("[BlenderBridge-DataHub] 错误: 在 EXR 标头中找不到 Cryptomatte 清单。")
        return None

    target_id_hash = manifest.get(object_name)
    if not target_id_hash:
        print(f"[BlenderBridge-DataHub] 错误: 在 Cryptomatte 清单中找不到对象 '{object_name}'。可用对象: {list(manifest.keys())}")
        return None
    
    target_id_float = hex_to_float(target_id_hash)
    
    id_channel_name = f'{prefix}00.R'
    if id_channel_name not in header['channels']:
        print(f"[BlenderBridge-DataHub] 错误: 在 EXR 文件中找不到 Cryptomatte 通道 '{id_channel_name}'。")
        return None

    id_r_channel_str = exr_file.channel(id_channel_name, Imath.PixelType(Imath.PixelType.FLOAT))
    ids_r = np.frombuffer(id_r_channel_str, dtype=np.float32).reshape((height, width))
    
    mask_np = np.isclose(ids_r, target_id_float).astype(np.float32)
    
    mask_tensor = torch.from_numpy(mask_np).unsqueeze(0)
    print(f"[BlenderBridge-DataHub] 已为 '{object_name}' 成功创建 Cryptomatte 遮罩。")
    return mask_tensor

def process_multilayer_exr(file_path, metadata):
    """根据Blender插件提供的'基础通道名'，并结合节点自身的'组件'知识，智能地提取通道。"""
    if not OPENEXR_SUPPORT:
        return {}

    channel_map = metadata.get("channel_map")
    if not channel_map:
        print("[BlenderBridge-DataHub] 警告: multilayer_exr 类型缺少 'channel_map' 元数据。无法提取通道。")
        return {}

    outputs = {}
    try:
        exr_file = OpenEXR.InputFile(file_path)
        header = exr_file.header()
        dw = header['dataWindow']
        width, height = (dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1)
        all_channels_in_file = list(header['channels'].keys())
        
        print(f"[BlenderBridge-DataHub] 使用 Blender 提供的 Channel Map: {channel_map}")
        
        COMPONENT_MAP = {
            'image': ['.R', '.G', '.B'], 'depth': ['.Z'], 'mist': ['.Z'],
            'normal': ['.X', '.Y', '.Z'], 'position': ['.X', '.Y', '.Z'], 'vector': ['.X', '.Y', '.Z', '.W'],
            'diffuse_direct': ['.R', '.G', '.B'], 'diffuse_color': ['.R', '.G', '.B'],
            'glossy_direct': ['.R', '.G', '.B'], 'glossy_color': ['.R', '.G', '.B'],
            'volume_direct': ['.R', '.G', '.B'], 'emission': ['.R', '.G', '.B'],
            'environment': ['.R', '.G', '.B'], 'shadow': ['.R', '.G', '.B'], 'ambient_occlusion': ['.R', '.G', '.B']
        }

        if 'combined' in channel_map and 'image' not in channel_map:
            channel_map['image'] = channel_map.pop('combined')

        for out_name, base_name in channel_map.items():
            if out_name not in COMPONENT_MAP:
                continue
            
            components = COMPONENT_MAP[out_name]
            full_channels_to_find = [f"{base_name}{c}" for c in components]
            
            # Vector pass can have 4 channels, but we only use 3
            if out_name == 'vector' and len(full_channels_to_find) == 4:
                full_channels_to_find = full_channels_to_find[:3]

            if all(c in all_channels_in_file for c in full_channels_to_find):
                if len(full_channels_to_find) == 1:
                    ch_str = exr_file.channel(full_channels_to_find[0])
                    ch_np = np.frombuffer(ch_str, dtype=np.float32)
                    img_np = np.clip(np.dstack((ch_np, ch_np, ch_np)), 0, 1)
                    outputs[out_name] = torch.from_numpy(img_np).view(height, width, 3)[None,]
                else: # RGB or VEC
                    ch_strs = [exr_file.channel(c) for c in full_channels_to_find]
                    ch_nps = [np.frombuffer(s, dtype=np.float32) for s in ch_strs]
                    img_np = np.clip(np.dstack(ch_nps), 0, 1)
                    outputs[out_name] = torch.from_numpy(img_np).view(height, width, 3)[None,]
            else:
                # This warning is now very specific and useful
                print(f"[BlenderBridge-DataHub] 警告: 未能为 '{out_name}' 找到所有必需的组件。尝试寻找: {full_channels_to_find}")

        extracted = list(outputs.keys())
        if extracted:
            print(f"[BlenderBridge-DataHub] 成功提取通道: {extracted}")

        return outputs
        
    except Exception as e:
        print(f"[BlenderBridge-DataHub] 处理 EXR 文件 '{file_path}' 时出错: {e}")
        return {}

class BlenderBridge_DataHub:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": { "bridge_pipe": ("BRIDGE_PIPE",) },
        }

    RETURN_TYPES = (
        "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", 
        "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE",
    )
    RETURN_NAMES = (
        "image", "depth", "mist", "normal", "position", "vector",
        "diffuse_direct", "diffuse_color", "glossy_direct", "glossy_color",
        "volume_direct", "emission", "environment", "shadow", "ambient_occlusion",
    )
    FUNCTION = "execute"
    CATEGORY = "Blender Bridge"

    def execute(self, bridge_pipe):
        print(f"[BlenderBridge-DataHub] 开始处理管道: {bridge_pipe}")
        
        files = bridge_pipe.get("files", [])
        metadata = bridge_pipe.get("metadata", {})
        
        outputs = {name: None for name in self.RETURN_NAMES}
        
        main_file = files[0] if files else {}
        file_path = main_file.get("path")
        render_type = main_file.get("type")

        processed_outputs = {}
        if render_type == 'multilayer_exr':
            if file_path and file_path.lower().endswith('.exr'):
                print(f"[BlenderBridge-DataHub] 检测到多层 EXR，使用元数据 channel_map 进行处理。")
                processed_outputs = process_multilayer_exr(file_path, metadata)
            else:
                 print(f"[BlenderBridge-DataHub] 错误: render_type 为 'multilayer_exr' 但文件不是 .exr 或路径无效。")
        
        elif render_type == 'standard':
            print(f"[BlenderBridge-DataHub] 检测到标准图像，仅加载主图像。")
            if file_path:
                processed_outputs['image'] = handle_standard_image(file_path)
        
        else:
            print(f"[BlenderBridge-DataHub] 未知的 render_type: '{render_type}' 或无文件。将尝试后备加载。")
            if file_path:
                processed_outputs['image'] = handle_standard_image(file_path)

        outputs.update(processed_outputs)

        h, w = 512, 512 # 如果没有任何图像，则为默认尺寸
        if outputs.get('image') is not None:
            h = outputs['image'].shape[1]
            w = outputs['image'].shape[2]

        for i, name in enumerate(self.RETURN_NAMES):
            if outputs.get(name) is None:
                # 仅当期望处理多层EXR时才打印警告
                if render_type == 'multilayer_exr':
                    print(f"[BlenderBridge-DataHub] 警告: 未在 channel_map 中找到或提取通道 '{name}'。将输出一个 {h}x{w} 的黑色图像。")
                
                return_type = self.RETURN_TYPES[i]
                if return_type == "IMAGE":
                    outputs[name] = torch.zeros((1, h, w, 3), dtype=torch.float32, device="cpu")
        
        print(f"[BlenderBridge-DataHub] 管道处理完成。")
        return tuple(outputs[name] for name in self.RETURN_NAMES) 