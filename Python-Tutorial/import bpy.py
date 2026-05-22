bl_info = {
    "name": "My Addon Name",
    "description": "Description of this addon",
    "author": "Authors name",
    "version": (0, 0, 1),
    "blender": (2, 9, 0),
    "location": "View3D",
    "warning": "This addon is still in development.",
    "wiki_url": "",
    "category": "Object" }
    
import bpy


class HelloWorldPanel(bpy.types.Panel):
    """Creates a Panel in the Object properties window"""
    bl_label = "Hello World Panel"
    bl_idname = "OBJECT_PT_hello"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = ""
    bl_category = 'Name your new tab'

    def draw(self, context):
        layout = self.layout

        row = layout.row()
        row.operator('shader.neon_operator')


class SHADER_OT_NEON(bpy.types.Operator):
    bl_label = "Add Neon Shader"
    bl_idname = 'shader.neon_operator'

    def execute(self, context):

        cur_frame= bpy.context.scene.frame_current

        material_neon = bpy.data.materials.new(name="Neon") #新しいマテリアルをNeonという名前で作成
        material_neon.use_nodes = True #ノードを使用するように設定   

        tree = material_neon.node_tree

        material_neon.node_tree.nodes.remove(material_neon.node_tree.nodes.get('Principled BSDF')) #デフォルトのノードを削除
        

        ###必要なノードを追加していく###
        material_output = material_neon.node_tree.nodes.get('Material Output') #マテリアル出力ノードを取得
        material_output.location = (400, 0) #マテリアル出力ノードの位置を設定

        #Adding Glass1 Node
        emiss_node = material_neon.node_tree.nodes.new('ShaderNodeEmission') #ガラスシェーダーノードを追加
        emiss_node.location = (200, 0) #ガラスシェーダーノードの位置を設定

        emiss_node.inputs[0].default_value = (0.6, 0.75, 1, 1) #ガラスシェーダーの色を赤に設定
        emiss_node.inputs[1].default_value = 2

        #Keyframe
        emiss_node.inputs[1].keyframe_insert("default_value", frame = cur_frame)

        data_path = f'nodes["{emiss_node.name}"].inputs[1].default_value'

        fcurves = tree.animation_data.action.fcurves #f-Curve一覧を取得
        fc = fcurves.find(data_path) #Emmision strengthのアニメーションカーブを探す
        if fc:
            new_mode = fc.modifiers.new('NOISE')# add noise modifier to f-curve
            new_mode.depth = 1#ノイズの複雑さ



        material_neon.node_tree.links.new(emiss_node.outputs[0], material_output.inputs[0]) #混合ノードの出力をマテリアル出力ノードの入力に接続

        #選択されているオブジェクトにマテリアルを割り当てる
        bpy.context.object.active_material = material_neon   
        return {'FINISHED'}


def register():
    bpy.utils.register_class(HelloWorldPanel)
    bpy.utils.register_class(SHADER_OT_NEON)


def unregister():
    bpy.utils.unregister_class(HelloWorldPanel)
    bpy.utils.unregister_class(SHADER_OT_NEON)


if __name__ == "__main__":
    register()