"""
__init__.py for ComfyUI-Blender-Bridge-node
"""

# Import node classes
from .nodes.receiver import BlenderBridge_Receiver
from .nodes.hub import BlenderBridge_DataHub
from .nodes.sender import BlenderBridge_Sender

# A dictionary that maps class names to class objects
NODE_CLASS_MAPPINGS = {
    "BlenderBridge_Receiver": BlenderBridge_Receiver,
    "BlenderBridge_DataHub": BlenderBridge_DataHub,
    "BlenderBridge_Sender": BlenderBridge_Sender,
}

# A dictionary that contains the friendly name displayed on the front-end
NODE_DISPLAY_NAME_MAPPINGS = {
    "BlenderBridge_Receiver": "Blender Bridge Receiver",
    "BlenderBridge_DataHub": "Blender Bridge Data Hub",
    "BlenderBridge_Sender": "Blender Bridge Sender",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"] 