import bpy

class STRING_OT_up(bpy.types.Operator):
    bl_idname = "string_anim.up"
    bl_label = "Up"

    def execute(self, context):

        obj = context.active_object

        if not obj or not obj.data.shape_keys:
            self.report({'ERROR'}, "Shape Keyがありません")
            return {'CANCELLED'}

        frame = context.scene.frame_current

        key = obj.data.shape_keys.key_blocks["up"]

        # frame
        context.scene.frame_set(frame)
        key.value = 0
        key.keyframe_insert(data_path="value")

        # frame + 1
        context.scene.frame_set(frame + 1)
        key.value = 1
        key.keyframe_insert(data_path="value")

        # frame + 2
        context.scene.frame_set(frame + 2)
        key.value = 0
        key.keyframe_insert(data_path="value")

        return {'FINISHED'}


class STRING_OT_down(bpy.types.Operator):
    bl_idname = "string_anim.down"
    bl_label = "Down"

    def execute(self, context):

        obj = context.active_object

        if not obj or not obj.data.shape_keys:
            self.report({'ERROR'}, "Shape Keyがありません")
            return {'CANCELLED'}

        frame = context.scene.frame_current

        key = obj.data.shape_keys.key_blocks["down"]

        # frame
        context.scene.frame_set(frame)
        key.value = 0
        key.keyframe_insert(data_path="value")

        # frame + 1
        context.scene.frame_set(frame + 1)
        key.value = 1
        key.keyframe_insert(data_path="value")

        # frame + 2
        context.scene.frame_set(frame + 2)
        key.value = 0
        key.keyframe_insert(data_path="value")

        return {'FINISHED'}


# ------------------------
# Panel
# ------------------------

class STRING_PT_panel(bpy.types.Panel):
    bl_label = "String Animation"
    bl_idname = "STRING_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "String"

    def draw(self, context):

        layout = self.layout

        layout.operator("string_anim.up")
        layout.operator("string_anim.down")


# ------------------------
# Register
# ------------------------

classes = [
    STRING_OT_up,
    STRING_OT_down,
    STRING_PT_panel
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()