bl_info = {
    "name": "Generate Crowd Animation",
    "description": "HumGen3D を使って群衆(ひな壇配置)を一括生成するアドオン",
    "author": "Authors name",
    "version": (0, 0, 2),
    "blender": (4, 2, 9),
    "location": "View3D > Sidebar > Crowd Animation",
    "warning": "This addon is still in development.",
    "wiki_url": "",
    "category": "Animation",
}


# ============================================================
# インポート
# ============================================================
# bpy / bpy.props : Blender Python API 本体と、Property 型(IntProperty 等)
# addon_utils     : Blender 標準アドオン (Rigify) を Python から有効化するため
# random          : 性別・プリセット・身長をランダム選択するため
# Human           : HumGen3D V4 の Python API。1人ぶんの「人間」を表すクラス
import bpy
import addon_utils
import random
from bpy.props import IntProperty, FloatProperty, PointerProperty
from HumGen3D import Human


# ============================================================
# PropertyGroup: パネル UI から調節できる配置パラメータ群
# ============================================================
# Blender の慣例として、UI から触れるパラメータは PropertyGroup にまとめて
# Scene 等にぶら下げる。後ほど register() で
#   bpy.types.Scene.crowd_animation_props = PointerProperty(type=CrowdAnimationProperties)
# として登録するので、コードからは context.scene.crowd_animation_props.xxx でアクセス可能になる。
# ----------------------------------------------------------------
class CrowdAnimationProperties(bpy.types.PropertyGroup):#UI から調整できるパラメータを定義するクラス
    cols: IntProperty(
        name="Columns",
        description="横方向(X)に並べる人数",
        default=3, min=1, max=20,
    )#列数を指定するプロパティ。IntProperty を使って整数型のプロパティを定義
    rows: IntProperty(
        name="Rows (Steps)",
        description="段数(Y方向)。奥に行くほど高くなる",
        default=5, min=1, max=20,
    )
    col_spacing: FloatProperty(
        name="Column Spacing",
        description="列(X)間の距離 [m]",
        default=0.7, min=0.0, max=10.0,
    )
    row_spacing: FloatProperty(
        name="Row Spacing",
        description="段(Y)間の距離 [m]。奥に行くほど +Y 方向に離れる",
        default=0.6, min=0.0, max=10.0,
    )
    step_height: FloatProperty(
        name="Step Height",
        description="1段ぶんの高さ [m](ひな壇の段差)",
        default=0.3, min=0.0, max=10.0,
    )
    height_min_cm: FloatProperty(
        name="Min",
        description="身長の下限 [cm]",
        default=160.0, min=50.0, max=250.0,
    )
    height_max_cm: FloatProperty(
        name="Max",
        description="身長の上限 [cm]",
        default=165.0, min=50.0, max=250.0,
    )
    seed: IntProperty(
        name="Seed",
        description="0 = 完全ランダム / 正の整数 = その値で固定して並びを再現可能",
        default=0, min=0,
    )


# ============================================================
# ひな壇配置の本体ロジック(オペレータから呼ばれる純粋関数)
# ============================================================
def spawn_human(x: float, y: float, z: float,
                height_min_cm: float, height_max_cm: float) -> Human:
    """1人ぶんの人間を指定位置に生成する。

    手順:
      (1) 性別とプリセットをランダム選択
      (2) Human.from_preset() でシーンに人間(リグ+ボディ+髪)を追加
      (3) テクスチャ解像度を 512px(low)に落として軽量化
      (4) 身長を指定範囲でランダムに設定
      (5) 服(アウトフィット + 靴)をランダムに装着
      (6) リグを Rigify に変換
      (7) 両腕の IK_FK プロパティを 1.0(FK)に切り替え
      (8) ワールド座標に配置
    """

    # (1) 性別 → プリセット名 のランダム選択
    gender = random.choice(["male", "female"])
    preset = random.choice(Human.get_preset_options(gender))

    # (2) プリセットから人間を生成(ここで Blender シーンにメッシュ&アーマチュアが追加される)
    human = Human.from_preset(preset)

    # (3) テクスチャを低解像度(~512px)に。多人数生成でも GPU メモリ・描画を軽くする。
    #     "high"=4K / "medium"=1K / "low"=512px。from_preset 直後に呼ぶ必要がある。
    human.skin.texture.set_resolution("low")

    # (4) 身長を cm 単位で設定。内部でシェイプキーとボーン長が同時に調整される。
    human.height.set(random.uniform(height_min_cm, height_max_cm))

    # (5) 服と靴をランダムに装着。装着メッシュは自動でリグにペアレント&ウェイト付与される。
    human.clothing.outfit.set_random()
    human.clothing.footwear.set_random()

    # (6) HumGen 標準リグを Rigify に変換。Rigify アドオンが事前に enable されている必要がある。
    human.pose.rigify.generate()

    # (7) 両腕を FK モードに。
    #     Rigify 腕は upper_arm_parent.L/R に IK_FK というカスタムプロパティを持っており、
    #     0.0 = IK / 1.0 = FK。FK 状態にしておくと手の振り上げ等のアニメが直接付けやすい。
    rig = human.objects.rig
    rig.pose.bones["upper_arm_parent.L"]["IK_FK"] = 1.0
    rig.pose.bones["upper_arm_parent.R"]["IK_FK"] = 1.0

    # (8) ワールド座標に移動(Rigify 化後でも human.location でリグ全体を動かせる)。
    human.location = (x, y, z)

    return human


def build_hinadan(props: "CrowdAnimationProperties"):
    """PropertyGroup の値に従って 3列 × N段 のひな壇配置で群衆を生成する。

    座標系メモ(Blender フロントビュー = Numpad 1):
      - X: 横(列)方向。X=0 を中心に左右対称に並ぶ
      - Y: 奥行き方向。+Y 側ほど「奥」(画面の奥に向かう)
      - Z: 高さ。奥の段ほど Z が高くなり、ひな壇の段差が出る
    """

    # 乱数シード固定。0 のときはシードしない(=毎回違う組み合わせ)。
    if props.seed != 0:
        random.seed(props.seed)

    # Rigify アドオンを有効化(すでに有効ならノーオペ)。
    # spawn_human() の中で human.pose.rigify.generate() を呼ぶ前に必須。
    addon_utils.enable("rigify")

    humans = []

    # 列方向(X)の中央寄せオフセット。
    # 例: cols=3, col_spacing=0.7 なら col_offset = -0.7 で、x は [-0.7, 0.0, +0.7]。
    col_offset = -(props.cols - 1) / 2.0 * props.col_spacing

    # 段(奥行き)ループ。row=0 が最前列、最後の row が最後列(=最も高い)。
    for row in range(props.rows):
        y = row * props.row_spacing   # 奥行き: 後ろの段ほど +Y 側
        z = row * props.step_height   # 高さ : 後ろの段ほど高い

        # 同じ段の中で COLS 人ぶん横方向に並べる
        for col in range(props.cols):
            x = col_offset + col * props.col_spacing

            # 進捗ログ(Blender のシステムコンソールで確認できる)
            print(f"[Crowd] row={row+1}/{props.rows} col={col+1}/{props.cols}"
                  f" -> ({x:.2f}, {y:.2f}, {z:.2f})")

            humans.append(spawn_human(
                x, y, z,
                props.height_min_cm,
                props.height_max_cm,
            ))

    print(f"[Crowd] Done. {len(humans)} humans placed.")
    return humans


# ============================================================
# パネル UI(View3D > サイドバー > Crowd Animation タブ)
# ============================================================
class CrowdAnimationPanel(bpy.types.Panel):
    bl_label = "Crowd Animation"
    bl_idname = "CROWD_PT_crowd_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Crowd Animation'

    def draw(self, context):
        layout = self.layout
        # register() で Scene に取り付けた PropertyGroup を取得
        props = context.scene.crowd_animation_props

        layout.label(text="Generate Crowd Animation", icon='ANIM')

        # --- 配置(Layout)パラメータ ---
        box = layout.box()
        box.label(text="Layout", icon='MESH_GRID')
        box.prop(props, "cols")
        box.prop(props, "rows")
        box.prop(props, "col_spacing")
        box.prop(props, "row_spacing")
        box.prop(props, "step_height")

        # --- 身長(Height)パラメータ ---
        box = layout.box()
        box.label(text="Height (cm)", icon='ARROW_LEFTRIGHT')
        row = box.row(align=True)
        row.prop(props, "height_min_cm")
        row.prop(props, "height_max_cm")

        # --- 乱数(Random)パラメータ ---
        box = layout.box()
        box.label(text="Random", icon='FILE_REFRESH')
        box.prop(props, "seed")

        # --- 実行ボタン ---
        layout.separator()
        layout.operator('crowd.generate', icon='OUTLINER_OB_ARMATURE')


# ============================================================
# オペレータ: ボタンが押されたときに実行される処理
# ============================================================
class CROWD_OT_GENERATE(bpy.types.Operator):
    bl_idname = "crowd.generate"
    bl_label = "Generate Hina-dan"
    bl_description = "現在のパラメータで群衆(ひな壇配置)を生成する"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.crowd_animation_props

        # 入力バリデーション: Min が Max を超えていたらキャンセル
        if props.height_min_cm > props.height_max_cm:
            self.report({'ERROR'}, "Height Min が Max を超えています")
            return {'CANCELLED'}

        humans = build_hinadan(props)
        self.report({'INFO'}, f"{len(humans)} 体生成しました")
        return {'FINISHED'}


# ============================================================
# 登録 / 解除
# ============================================================
# Blender 2.8 以降では bpy.utils.register_module は廃止されており、
# 個別に bpy.utils.register_class でクラスを登録する必要がある。
# 順序が重要: PropertyGroup を先に登録 → そのあと Scene にぶら下げる。
classes = (
    CrowdAnimationProperties,
    CrowdAnimationPanel,
    CROWD_OT_GENERATE,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # PropertyGroup をシーン全体で共有するため Scene にぶら下げる
    bpy.types.Scene.crowd_animation_props = PointerProperty(type=CrowdAnimationProperties)


def unregister():
    # 登録と逆順で外す(依存関係を壊さないため)
    del bpy.types.Scene.crowd_animation_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
