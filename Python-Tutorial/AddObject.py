bl_info = { #アドオンの情報を記述する辞書
    "name": "My Addon Name", #アドオンの名前
    "description": "Description of this addon", #アドオンの説明
    "author": "Authors name",
    "version": (0, 0, 1), #アドオンのバージョン
    "blender": (4, 2, 9), #このアドオンが対応しているBlenderのバージョン
    "location": "View3D > Tool", #アドオンの機能がどこで使えるか
    "warning": "This addon is still in development.", 
    "wiki_url": "",
    "category": "Add Mesh" }#アドオンのカテゴリ
    


import bpy

#Panelの作成    
class Test_Panel(bpy.types.Panel):
    bl_label = "Test Panel" #パネルの名前
    bl_idname = "OBJECT_PT_test_panel"
    #クラスのID　OBJECT_PT_はオブジェクトモードで表示することを意味する
    bl_space_type = 'VIEW_3D' #どこに配置するか
    bl_region_type = 'UI' #領域
    bl_category = 'NewTab'#タブの名前

    def draw(self, context): #UIの描画
        layout = self.layout #UIのレイアウトを取得
        layout.scale_y = 1.5 #UIの縦のスケールを変更
       

        row  = layout.row()#　改行
        row.label(text="Add an object", icon='CUBE')
        row  = layout.row()
        row.operator("mesh.primitive_cube_add", icon='CUBE') #add cube
        row  = layout.row()
        row.operator("mesh.primitive_uv_sphere_add", icon='SPHERE')#add sphere
        row  = layout.row()
        row.operator("object.text_add", icon='FONT_DATA')#add text



class PanelA(bpy.types.Panel):
    bl_label = "scale" #パネルの名前
    bl_idname = "PT_panelA"
    #クラスのID　OBJECT_PT_はオブジェクトモードで表示することを意味する
    bl_space_type = 'VIEW_3D' #どこに配置するか
    bl_region_type = 'UI' #領域
    bl_category = 'NewTab'#タブの名前
    bl_parent_id = "OBJECT_PT_test_panel" #親パネルのIDを指定、入れ子構造にすることができる
    bl_options = {'DEFAULT_CLOSED'} #パネルを閉じている状態で表示する


    def draw(self, context): #UIの描画
        layout = self.layout #UIのレイアウトを取得
        obj = context.object #現在選択されているオブジェクトを取得

        row  = layout.row()#　改行
        row.label(text="select an option to scale your objects", icon='FONT_DATA') #テキストとアイコンを表示
        row  = layout.row()
        row.operator("transform.resize", text="Scale 2x", icon='ARROW_LEFTRIGHT').value = (2, 2, 2) #オペレーターを呼び出す,valueでスケールの値を指定
        row  = layout.row()
        layout.scale_y = 1.5 #UIの縦のスケールを変更
        col = layout.column() #列を作成
        col.prop(obj, "scale")#オブジェクトのスケールを変更するプロパティを表示


class PanelB(bpy.types.Panel):
    bl_label = "Specials" #パネルの名前
    bl_idname = "PT_panelB"
    #クラスのID　OBJECT_PT_はオブジェクトモードで表示することを意味する
    bl_space_type = 'VIEW_3D' #どこに配置するか
    bl_region_type = 'UI' #領域
    bl_category = 'NewTab'#タブの名前
    bl_parent_id = "OBJECT_PT_test_panel" #親パネルのIDを指定、入れ子構造にすることができる
    bl_options = {'DEFAULT_CLOSED'} #パネルを閉じている状態で表示する

    def draw(self, context): #UIの描画
        layout = self.layout #UIのレイアウトを取得
        row  = layout.row()#　改行

        row.label(text="select a Special Option", icon='FONT_DATA') #テキストとアイコンを表示
        row  = layout.row()#　改行
        row.operator("object.shade_smooth")#shade smooth"
        row  = layout.row()#　改行
        row.operator("object.subdivision_set") #subdivision modifier
        row  = layout.row()#　改行
        row.operator("object.modifier_add")#add modifier
        


def register():
    bpy.utils.register_class(Test_Panel)
    bpy.utils.register_class(PanelA)
    bpy.utils.register_class(PanelB)

def unregister():
    bpy.utils.unregister_class(Test_Panel)
    bpy.utils.unregister_class(PanelA)
    bpy.utils.unregister_class(PanelB)

if __name__ == "__main__":    register()