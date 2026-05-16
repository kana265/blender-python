#addon settings
bl_info = {
    "name": "NGon Finder",
    "author": "Kana",
    "version": (1, 0),
    "blender": (4, 20, 9),
    "location": "View3D > sidebar",
    "description": "find N-gon faces",
    "warning": "",
    "doc_url": "",
    "category": "Mesh",
}

import bpy
import bmesh
#make UI in 3d view

class HelloWorldPanel(bpy.types.Panel):
    """Creates a Panel in the Object properties window"""
    bl_label = "NGon Finder"
    bl_idname = "OBJECT_PT_hello"
    bl_space_type = 'VIEW_3D' #3D view 
    bl_region_type = 'UI'
    bl_category ="NGon-Finder" #addon's name
    
    #UI contents
    def draw(self, context):
        # call oprrator when you push the button
        self.layout.row().operator("object.simple_operator")


#make original operator
class SimpleOperator(bpy.types.Operator):
    """Tooltip"""
    bl_idname = "object.simple_operator"
    bl_label = "Find"# label of button


    def execute(self, context):
        bpy.ops.object.mode_set(mode= 'EDIT')
        #switch to face selection mode
        bpy.ops.mesh.select_mode(type = 'FACE')

        # Get the active mesh
        obj = bpy.context.edit_object
        me = obj.data


        # Get a BMesh representation
        bm = bmesh.from_edit_mesh(me)

        #choose N-gon face
        for face in bm.faces:
            if len(face.verts) == 4:
                face.select_set(False)
            else: 
                face.select_set(True)

        # Show the updates in the viewport
        # and recalculate n-gon tessellation.
        bmesh.update_edit_mesh(me, loop_triangles=True)
            
        return {'FINISHED'}



def register():
    bpy.utils.register_class(HelloWorldPanel)
    bpy.utils.register_class(SimpleOperator)


def unregister():
    bpy.utils.unregister_class(HelloWorldPanel)
    bpy.utils.unregister_class(SimpleOperator)


if __name__ == "__main__":
    register()
