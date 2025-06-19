# ComfyUI-Blender Bridge (节点端)

这是一个为 [ComfyUI](https://github.com/comfyanonymous/ComfyUI) 设计的节点包，它充当了与 Blender 插件通信的服务器端桥梁，实现了 Blender 和 ComfyUI 之间稳定、高效、实时的双向通信。

这个项目是 **Blender-ComfyUI Bridge** 系统的一部分，负责在 ComfyUI 内部接收任务、解析数据，并将处理结果发送回 Blender。要完整使用此系统，您还需要在 Blender 中安装对应的[客户端插件](https://github.com/ageless-h/Blender-ComfyUI-Bridge)。

## ✨ 功能特性 (节点端)

*   **交互式工作流集成**: 节点以交互模式运行，实时接收 Blender 发送的图像和元数据，并通过 `DataHub` 节点将其分解为多个渲染通道，供您在 ComfyUI 中自由构建和试验工作流。
*   **强大的 EXR 数据解析**:
    *   **多通道 EXR 支持**: `DataHub` 节点可以解析多层 EXR 文件，将所有渲染通道（如 `Depth`, `Mist`, `Normal`, `AO`, `Diffuse Color` 等）分离成独立的图像输出。
    *   **动态通道映射 (`channel_map`)**: 节点不硬编码通道名称，而是根据 Blender 插件发送的 `channel_map` 元数据动态查找，提供了极高的灵活性和兼容性。
*   **健壮的通信协议**:
    *   **Blender -> ComfyUI**: 使用 **ZMQ (REP/REQ)** 接收来自 Blender 的多部分消息（元数据 + 图像二进制数据）。
    *   **ComfyUI -> Blender**: 使用 **HTTP** 将处理完成的图像数据发送回 Blender。
*   **智能数据返回**:
    *   `Sender` 节点能够判断 Blender 的位置。如果是在本机，它会通过共享文件路径的方式返回结果，速度极快；如果是远程，则会通过网络发送图像的二进制数据，兼容跨设备部署。
*   **实时连接测试**:
    *   内置 `ping/pong` 机制，方便 Blender 插件测试与 ComfyUI 服务器的连接状态。
*   **依赖项检查**: 节点会自动检测 `OpenEXR-python` 依赖是否存在，并在缺失时给出明确的安装提示。

## 🚀 安装指南

1.  **克隆或下载本仓库**:
    ```bash
    cd ComfyUI/custom_nodes/
    git clone https://github.com/ageless-h/ComfyUI-Blender-Bridge-node.git
    ```
    或者，从 GitHub 下载 ZIP 压缩包并解压到 `ComfyUI/custom_nodes/` 目录下。

2.  **安装依赖项**:
    为了完整支持 EXR 多通道功能，您需要安装 `OpenEXR-python`。请在您的 ComfyUI Python 环境中运行：
    ```bash
    pip install OpenEXR-python msgspec
    ```
    *   `OpenEXR-python`: 用于解析 EXR 文件。
    *   `msgspec`: 用于高效地处理 ZMQ 消息中的元数据。

3.  **重启 ComfyUI**:
    重启 ComfyUI，您应该能在节点菜单的 `Blender Bridge` 分类下找到以下节点：
    *   **Receiver**: 启动 ZMQ 服务器，接收 Blender 数据。是一切流程的起点。
    *   **DataHub**: （工作流核心）解析收到的数据，特别是将 EXR 分解为多个渲染通道。
    *   **Sender**: 将最终的图像结果通过 HTTP 发送回 Blender。

## 🔧 使用方法

本节点包专注于提供一个灵活的交互式工作流。

1.  在 ComfyUI 中，按以下顺序连接节点：
    `Receiver` -> `DataHub` -> ... (您的图像处理节点) ... -> `Sender`

2.  从 `DataHub` 的各个输出端口（`image`, `depth`, `normal` 等）拉出您需要的渲染通道，将它们用作您工作流的输入。

3.  将您最终处理好的图像连接到 `Sender` 节点的 `image` 输入端口。

4.  在 Blender 插件中发送图像。图像和数据会出现在您的 ComfyUI 工作流中，处理完成后结果会自动返回 Blender。

---

## 💻 技术实现细节 (供开发者参考)

### ZMQ 消息结构

节点接收到的 ZMQ 消息包含 **2** 个部分：

1.  **Part 1: 元数据 (Metadata)**
    *   **类型**: `msgspec` 编码的字典。
    *   **内容**: 包含图像信息 (`render_type`) 和返回地址 (`return_info`) 等。

2.  **Part 2: 图像二进制数据 (Image Bytes)**
    *   **类型**: 原始字节流 (`bytes`)。
    *   **内容**: `.png` 或 `.exr` 文件的完整二进制内容。

**接收逻辑 (`receiver.py`) 必须使用 `socket.recv_multipart()` 来正确解析这两个部分。**

### 关键元数据字段

*   `render_type` (字符串): `'standard'` 或 `'multilayer_exr'`。`DataHub` 节点根据此字段决定解析逻辑。
*   `channel_map` (字典): 仅在 `render_type` 为 `'multilayer_exr'` 时提供。用于将 ComfyUI 的通道名 (key) 映射到 EXR 文件中实际的通道名 (value)。
*   `return_info` (字典): 包含 Blender HTTP 服务器的地址 (`blender_server_address`) 和目标图像数据块的名称 (`image_datablock_name`)，供 `Sender` 节点使用。 