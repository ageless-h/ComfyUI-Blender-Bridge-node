# ComfyUI-Blender-Bridge-node

一个专为 Blender 与 ComfyUI 之间"版本2.0"桥接方案设计的自定义节点包。其核心目标是成为一个高性能、全自动、可扩展的数据接收与工作流控制中心。

## 核心功能

- **双模操作**:
    1.  **全自动模式**: Blender 可以直接发送一个包含工作流的请求。本节点包会自动更新工作流中的文件路径，通过API提交执行，并在完成后清理临时文件，实现从Blender到最终出图的全程自动化。
    2.  **交互模式**: 在ComfyUI中搭建工作流，使用 `Blender Bridge Receiver` 节点作为起点，实时接收来自Blender的数据，并通过 `Blender Bridge Data Hub` 节点将Blender渲染的各种数据通道分发给下游节点。

- **高性能通信**: 使用 ZeroMQ (REP/REQ) 在后台线程中进行通信，确保ComfyUI主界面流畅不卡顿。

- **高级数据集成**: 
    - 完全由Blender插件主导，支持任意渲染通道（如深度、法线、Mist、AO、阴影等）。
    - 节点端通过解析Blender插件提供的 `channel_map` 元数据，动态、精确地提取EXR文件中的数据，具有极高的灵活性和可扩展性。

- **资源管理**: 在全自动模式下，能够通过WebSocket监控工作流状态，执行完毕后自动清理临时文件，防止磁盘空间被占用。

## 安装

1.  **克隆或下载项目**
    将本项目放置于 `ComfyUI/custom_nodes/` 目录下。
    ```bash
    cd ComfyUI/custom_nodes/
    git clone https://github.com/ageless-h/ComfyUI-Blender-Bridge-node.git
    ```

2.  **安装依赖项**
    进入项目目录并安装所需的Python库。
    ```bash
    cd ComfyUI/custom_nodes/ComfyUI-Blender-Bridge-node/
    pip install -r requirements.txt
    ```
    此命令将安装 `pyzmq`, `msgspec`, `websockets`, 和 `openexr`。

3.  **重启ComfyUI**
    完全重启ComfyUI以加载新节点。

## 节点用法

### 1. Blender Bridge Receiver (接收器)
- **类别**: `Blender Bridge`
- **功能**: 这是工作流的起点。它在后台启动一个服务器，持续监听来自Blender的数据。
- **模式**:
    - **自动模式**: 如果Blender发送了工作流，此节点不会输出任何东西，因为整个流程都在后台通过API处理。
    - **交互模式**: 如果只收到了数据（没有工作流），它会等待数据到达，然后将打包好的数据（`BRIDGE_PIPE`）传递给下游。
- **注意**: 此节点被设计为"实时"节点，能够即时响应Blender发送的新数据。

### 2. Blender Bridge Data Hub (数据中心)
- **类别**: `Blender Bridge`
- **输入**:
    - `bridge_pipe`: 接收来自 `Receiver` 节点的打包数据。
- **输出**:
    - 动态输出Blender渲染的各种通道，如 `image`, `depth`, `normal`, `mist`, `shadow`, `ambient_occlusion` 等15个标准通道。
- **功能**: 这是数据的分发中心。它会根据Blender插件元数据中的 `render_type` 智能判断收到的数据是标准图像还是多层EXR。
    - **标准图像**: 直接输出到 `image` 端口。
    - **多层EXR**: 根据元数据中的 `channel_map`，精确地从EXR文件中提取出所有指定的通道，并输出到对应的端口。对于未提供或未找到的通道，会输出一个安全的黑色图像以防止工作流中断。

## 示例工作流
请参阅 `example_workflows/` 目录，了解如何设置交互式工作流，特别是高级的Cryptomatte控制流程。 