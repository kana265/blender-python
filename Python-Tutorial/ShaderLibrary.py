bl_info = {
    "name": "Shader Library",
    "description": "Description of this addon",
    "author": "Authors name",
    "version": (0, 0, 1),
    "blender": (4, 2, 9),
    "location": "View3D",
    "warning": "This addon is still in development.",
    "wiki_url": "",
    "category": "Add Shader" }
    

import bpy

class ShaderLibraryPanel(bpy.types.Panel):
    bl_label = "Shader Library"
    bl_idname = "SHADER_PT_shader_library"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Shader Library'

    def draw(self, context):
        layout = self.layout
        row  = layout.row()
        row.label(text="select a shader to be added", icon='SHADING_RENDERED')
        row = layout.row()
        row.operator('shader.diamond')#オペレーターを呼び出すときはbl_idnameを指定する

class SHADER_OT_DIAMOND(bpy.types.Operator):    
    bl_idname = "shader.diamond"
    bl_label = 'shader.diamond_operator' #  オペレーターの名前
    bl_description = "Add a diamond shader to the selected object"

    def execute(self, context):
        # ダイヤモンドシェーダーのノードを作成するコードをここに記述
        material_diamond = bpy.data.materials.new(name="Diamond") #新しいマテリアルをDiamondという名前で作成
        material_diamond.use_nodes = True #ノードを使用するように設定   

        material_diamond.node_tree.nodes.remove(material_diamond.node_tree.nodes.get('Principled BSDF')) #デフォルトのノードを削除
        

        ###必要なノードを追加していく###
        material_output = material_diamond.node_tree.nodes.get('Material Output') #マテリアル出力ノードを取得
        material_output.location = (-400, 0) #マテリアル出力ノードの位置を設定

        #Adding Glass1 Node
        glass1_node = material_diamond.node_tree.nodes.new('ShaderNodeBsdfGlass') #ガラスシェーダーノードを追加
        glass1_node.location = (-600, 0) #ガラスシェーダーノードの位置を設定

        glass1_node.inputs[0].default_value = (1, 0, 0, 1) #ガラスシェーダーの色を赤に設定
        glass1_node.inputs[2].default_value = 1.446

        #Adding Glass2 Node
        glass2_node = material_diamond.node_tree.nodes.new('ShaderNodeBsdfGlass') #ガラスシェーダーノードを追加
        glass2_node.location = (-600, -150) #ガラスシェーダーノードの位置を設定

        glass2_node.inputs[0].default_value = (0, 1, 0, 1) #ガラスシェーダーの色を緑に設定
        glass2_node.inputs[2].default_value = 1.450

        #Adding Glass3 Node
        glass3_node = material_diamond.node_tree.nodes.new('ShaderNodeBsdfGlass') #ガラスシェーダーノードを追加
        glass3_node.location = (-600, -300) #ガラスシェーダーノードの位置を設定

        glass3_node.inputs[0].default_value = (0, 0, 1, 1) #ガラスシェーダーの色を青に設定
        glass3_node.inputs[2].default_value = 1.455

        #Adding Glass4 Node
        glass4_node = material_diamond.node_tree.nodes.new('ShaderNodeBsdfGlass') #ガラスシェーダーノードを追加
        glass4_node.location = (-150, -150) #ガラスシェーダーノードの位置を設定

        glass4_node.inputs[0].default_value = (1, 1, 1, 1) #ガラスシェーダーの色を白に設定
        glass4_node.inputs[2].default_value = 1.460
        glass4_node.select = False


        #Create the add Shader node andregerence it as 'Add1'
        add1_node = material_diamond.node_tree.nodes.new('ShaderNodeAddShader') #シェーダーを加算するノードを追加
        add1_node.location = (-400, -50) #加算ノードの位置を設定
        add1_node.label = 'Add1' #加算ノードの名前をAdd1に設定
        add1_node.hide = True #加算ノードを非表示に設定
        add1_node.select = False #加算ノードを選択解除

        #Create the add Shader node andregerence it as 'Add2'
        add2_node = material_diamond.node_tree.nodes.new('ShaderNodeAddShader') #シェーダーを加算するノードを追加
        add2_node.location = (-100, -00) #加算ノードの位置を設定
        add2_node.label = 'Add2' #加算ノードの名前をAdd2に設定
        add2_node.hide = True #加算ノードを非表示に設定
        add2_node.select = False #加算ノードを選択解除

        #create the mix shader node
        mix1_node = material_diamond.node_tree.nodes.new('ShaderNodeMixShader') #シェーダーを混合するノードを追加
        mix1_node.location = (200, 0)
        mix1_node.select = False

        ##ノードを接続していく##
        material_diamond.node_tree.links.new(glass1_node.outputs[0], add1_node.inputs[0]) #ガラスシェーダー1の出力を加算ノード1の入力に接続
        material_diamond.node_tree.links.new(glass2_node.outputs[0], add1_node.inputs[1]) #ガラスシェーダー2の出力を加算ノード1の入力に接続
        material_diamond.node_tree.links.new(add1_node.outputs[0], add2_node.inputs[0]) #加算ノード1の出力を加算ノード2の入力に接続
        material_diamond.node_tree.links.new(glass3_node.outputs[0], add2_node.inputs[1]) #ガラスシェーダー3の出力を加算ノード2の入力に接続
        material_diamond.node_tree.links.new(add2_node.outputs[0], mix1_node.inputs[1]) #加算ノード2の出力を混合ノードのシェーダー入力1に接続
        material_diamond.node_tree.links.new(glass4_node.outputs[0], mix1_node.inputs[2]) #ガラスシェーダー4の出力を混合ノードのシェーダー入力2に接続
        material_diamond.node_tree.links.new(mix1_node.outputs[0], material_output.inputs[0]) #混合ノードの出力をマテリアル出力ノードの入力に接続

        #選択されているオブジェクトにマテリアルを割り当てる
        bpy.context.object.active_material = material_diamond   
        return {'FINISHED'}
    

def register():
    bpy.utils.register_class(ShaderLibraryPanel)
    bpy.utils.register_class(SHADER_OT_DIAMOND)    

def unregister():
    bpy.utils.unregister_class(ShaderLibraryPanel)
    bpy.utils.unregister_class(SHADER_OT_DIAMOND)
if __name__ == "__main__":
    register()