#ポップアップダイアログを表示する

import bpy

class WM_OT_myOp(bpy.types.Operator):
    """Open the Add Cube Dialog box"""
    bl_label = "Add Cube Dialog Box"
    bl_idname = "wm.myop"

    text = bpy.props.StringProperty(name = "Enter text", default= "")

    scale = bpy.props.FloatVectorProperty(name = "Scale", default = (1,1,1))

    def execute(self, context):
        
        t = self.text
        s = self.scale

        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = t
        obj.scale[0] = s[0]
        obj.scale[1] = s[1]
        obj.scale[2] = s[2]
        


        return {'FINISHED'}#operatorの戻り値はset型だから()ではなく{}
    
    def invoke(self, context, event):

        return context.window_manager.invoke_props_dialog(self)
    

def register():
    bpy.utils.register_class(WM_OT_myOp)

def unregister():
    bpy.utils.unregister_class(WM_OT_myOp)

if __name__ == "__main__":
    register()

    bpy.ops.wm.myop('INVOKE_DEFAULT')