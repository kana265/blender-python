bl_info = {
    "name": "Generate Crowd Animation",
    "description": "HumGen3D を使って群衆(ひな壇配置)を一括生成し、NLA でウェーブを付けるアドオン",
    "author": "Authors name",
    "version": (0, 0, 3),
    "blender": (4, 2, 9),
    "location": "View3D > Sidebar > Crowd Animation",
    "warning": "This addon is still in development.",
    "wiki_url": "",
    "category": "Animation",
}


# ============================================================
# インポート
# ============================================================
import bpy
import addon_utils
import random
from bpy.props import (
    IntProperty,
    FloatProperty,
    PointerProperty,
    FloatVectorProperty,
)
from HumGen3D import Human


# ============================================================
# 定数
# ============================================================
# Crowds (親) ─ crowd1, crowd2, ... (各回の生成結果) という階層を作る
PARENT_COLL_NAME = "Crowds"
CROWD_NAME_PREFIX = "crowd"

# NLA に積むトラック名(ウェーブ専用)
WAVE_TRACK_NAME = "WaveAnimation"

# 既定のウェーブアクション名(Action ピッカーが未設定のときの自動フォールバック)
DEFAULT_WAVE_ACTION_NAME = "wave_remap"


# ============================================================
# PropertyGroup: ひな壇生成パラメータ
# ============================================================
class CrowdAnimationProperties(bpy.types.PropertyGroup):
    cols: IntProperty(
        name="Columns",
        description="横方向(X)に並べる人数",
        default=3, min=1, max=20,
    )
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
        description="段(Y)間の距離 [m]",
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
# PropertyGroup: ウェーブアニメーションのパラメータ
# ============================================================
class WaveAnimationProperties(bpy.types.PropertyGroup):
    target_collection: PointerProperty(
        name="Target Crowd",
        description="ウェーブを適用する crowd* コレクション",
        type=bpy.types.Collection,
    )
    action: PointerProperty(
        name="Wave Action",
        description="NLA に積むアクション。未指定なら 'wave_remap' を自動で探す",
        type=bpy.types.Action,
    )
    start_frame: IntProperty(
        name="Start Frame",
        description="左端 (col=0) のリグが再生開始するフレーム",
        default=1, min=0,
    )
    col_delay: FloatProperty(
        name="Column Delay",
        description="1列ぶんずらすフレーム数。左→右へ波が伝搬する",
        default=3.0, min=0.0,
    )
    row_delay: FloatProperty(
        name="Row Delay",
        description="1段(奥行き)ぶんずらすフレーム数。0 なら横方向だけのウェーブ",
        default=0.0,
    )


# ============================================================
# PropertyGroup: コレクション単位のオフセット
# ============================================================
class CrowdOffsetProperties(bpy.types.PropertyGroup):
    target_collection: PointerProperty(
        name="Target Crowd",
        description="移動させたい crowd* コレクション",
        type=bpy.types.Collection,
    )
    offset: FloatVectorProperty(
        name="Offset",
        description="コレクション全体に加える XYZ オフセット [m]",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION',
    )


# ============================================================
# コレクション系ヘルパ
# ============================================================
def get_or_create_parent_collection() -> bpy.types.Collection:
    """親コレクション 'Crowds' を取得 or 作成。シーンに必ずリンクされている状態にする。"""
    parent = bpy.data.collections.get(PARENT_COLL_NAME)
    if parent is None:
        parent = bpy.data.collections.new(PARENT_COLL_NAME)

    scene_root = bpy.context.scene.collection
    if PARENT_COLL_NAME not in scene_root.children:
        try:
            scene_root.children.link(parent)
        except RuntimeError:
            # すでに別コレクション配下にリンクされている場合は無視
            pass
    return parent


def next_crowd_collection(parent: bpy.types.Collection) -> bpy.types.Collection:
    """parent.children を走査し、未使用の番号で crowdN コレクションを新規作成する。"""
    max_idx = 0
    for child in parent.children:
        if not child.name.startswith(CROWD_NAME_PREFIX):
            continue
        # "crowd12" や "crowd3.001" のような名前から番号を取り出す
        suffix = child.name[len(CROWD_NAME_PREFIX):].split('.')[0]
        try:
            idx = int(suffix)
        except ValueError:
            continue
        if idx > max_idx:
            max_idx = idx

    new_name = f"{CROWD_NAME_PREFIX}{max_idx + 1}"
    coll = bpy.data.collections.new(new_name)
    parent.children.link(coll)
    return coll


def reparent_human_objects(human: Human, target_coll: bpy.types.Collection):
    """Human が生成時に置かれたコレクションから target_coll に全オブジェクトを移し替える。"""
    rig = human.objects.rig
    objs = [rig] + list(rig.children_recursive)

    src_colls = set()
    for obj in objs:
        for c in list(obj.users_collection):
            src_colls.add(c)
            c.objects.unlink(obj)
        target_coll.objects.link(obj)

    # 空になった HumGen 由来コレクションは掃除(crowd* / Crowds / Scene Collection は保護)
    protected = {PARENT_COLL_NAME, target_coll.name, bpy.context.scene.collection.name}
    for c in src_colls:
        if c.name in protected:
            continue
        if len(c.objects) == 0 and len(c.children) == 0:
            try:
                bpy.data.collections.remove(c)
            except Exception:
                pass


# ============================================================
# 1人ぶん生成
# ============================================================
def spawn_human(x: float, y: float, z: float,
                height_min_cm: float, height_max_cm: float) -> Human:
    """1人ぶんの人間を指定位置に生成する。

    手順:
      (1) 性別とプリセットをランダム選択
      (2) Human.from_preset() でシーンに人間を追加
      (3) テクスチャ解像度を 512px に落として軽量化
      (4) 身長を指定範囲でランダム設定
      (5) 服+靴をランダム装着
      (6) Rigify 化
      (7) 両腕の IK_FK を 1.0 (FK) に
      (8) ワールド座標に配置
    """
    gender = random.choice(["male", "female"])
    preset = random.choice(Human.get_preset_options(gender))
    human = Human.from_preset(preset)

    human.skin.texture.set_resolution("low")
    human.height.set(random.uniform(height_min_cm, height_max_cm))
    human.clothing.outfit.set_random()
    human.clothing.footwear.set_random()

    human.pose.rigify.generate()
    rig = human.objects.rig
    rig.pose.bones["upper_arm_parent.L"]["IK_FK"] = 1.0
    rig.pose.bones["upper_arm_parent.R"]["IK_FK"] = 1.0

    human.location = (x, y, z)
    return human


# ============================================================
# ひな壇配置: humans[row][col] の 2次元配列で返す
# ============================================================
def build_hinadan(props: "CrowdAnimationProperties"):
    """PropertyGroup の値に従って群衆を生成し、(humans_2d, crowd_coll) を返す。

    - humans_2d: humans[row][col] の 2次元配列(Human オブジェクト)
    - crowd_coll: このコールで作られた crowdN コレクション
    """
    if props.seed != 0:
        random.seed(props.seed)

    # Rigify 必須
    addon_utils.enable("rigify")

    parent = get_or_create_parent_collection()
    crowd_coll = next_crowd_collection(parent)

    humans_2d: list[list[Human]] = []
    col_offset = -(props.cols - 1) / 2.0 * props.col_spacing

    for row in range(props.rows):
        y = row * props.row_spacing
        z = row * props.step_height
        row_humans: list[Human] = []

        for col in range(props.cols):
            x = col_offset + col * props.col_spacing

            print(f"[Crowd] {crowd_coll.name} row={row+1}/{props.rows} "
                  f"col={col+1}/{props.cols} -> ({x:.2f}, {y:.2f}, {z:.2f})")

            human = spawn_human(
                x, y, z,
                props.height_min_cm,
                props.height_max_cm,
            )
            reparent_human_objects(human, crowd_coll)

            # 後で 2次元配列を再構築できるように、行/列インデックスをリグに刻む
            rig = human.objects.rig
            rig["crowd_row"] = row
            rig["crowd_col"] = col

            row_humans.append(human)

        humans_2d.append(row_humans)

    # メタ情報をコレクションにも保存(後で参照用)
    crowd_coll["rows"] = props.rows
    crowd_coll["cols"] = props.cols

    total = props.rows * props.cols
    print(f"[Crowd] Done. {crowd_coll.name}: {props.rows}x{props.cols} = {total} humans.")
    return humans_2d, crowd_coll


# ============================================================
# ウェーブアニメーション
# ============================================================
def collect_rigs_2d(crowd_coll: bpy.types.Collection):
    """crowd* コレクション内のリグを (row, col) インデックスから 2次元配列に並べ直す。"""
    rigs = [
        obj for obj in crowd_coll.objects
        if obj.type == 'ARMATURE'
        and "crowd_row" in obj.keys()
        and "crowd_col" in obj.keys()
    ]
    if not rigs:
        return []

    rows = max(int(r["crowd_row"]) for r in rigs) + 1
    cols = max(int(r["crowd_col"]) for r in rigs) + 1

    grid: list[list[bpy.types.Object | None]] = [[None] * cols for _ in range(rows)]
    for r in rigs:
        grid[int(r["crowd_row"])][int(r["crowd_col"])] = r
    return grid


class WaveAnimator:
    """crowd コレクション内のリグに対し、アクションを時間差付きで NLA に積むクラス。

    使い方:
        WaveAnimator(crowd_coll, action,
                     start_frame=1, col_delay=3.0, row_delay=0.0).apply()

    挙動:
      - 各リグの NLA に WAVE_TRACK_NAME という名前のトラックを用意
      - col*col_delay + row*row_delay だけ後ろにずらして strip を貼る
      - 同名 strip は一度消してから貼り直す(再適用しやすい)
    """

    def __init__(
        self,
        crowd_coll: bpy.types.Collection,
        action: bpy.types.Action,
        start_frame: int = 1,
        col_delay: float = 3.0,
        row_delay: float = 0.0,
        track_name: str = WAVE_TRACK_NAME,
    ):
        self.crowd_coll = crowd_coll
        self.action = action
        self.start_frame = int(start_frame)
        self.col_delay = float(col_delay)
        self.row_delay = float(row_delay)
        self.track_name = track_name

    def _ensure_track(self, rig: bpy.types.Object) -> bpy.types.NlaTrack:
        if rig.animation_data is None:
            rig.animation_data_create()
        ad = rig.animation_data
        track = ad.nla_tracks.get(self.track_name)
        if track is None:
            track = ad.nla_tracks.new()
            track.name = self.track_name
        return track

    def _add_strip(self, rig: bpy.types.Object, row: int, col: int):
        offset = int(round(col * self.col_delay + row * self.row_delay))
        start = self.start_frame + offset

        track = self._ensure_track(rig)
        strip_name = f"wave_r{row}_c{col}"
        # 既存の同名 strip を消してから貼り直す(再適用しやすいように)
        for s in list(track.strips):
            if s.name == strip_name:
                track.strips.remove(s)
        return track.strips.new(strip_name, start, self.action)

    def apply(self) -> int:
        grid = collect_rigs_2d(self.crowd_coll)
        count = 0
        for row, row_rigs in enumerate(grid):
            for col, rig in enumerate(row_rigs):
                if rig is None:
                    continue
                self._add_strip(rig, row, col)
                count += 1
        return count


# ============================================================
# オペレータ
# ============================================================
class CROWD_OT_GENERATE(bpy.types.Operator):
    bl_idname = "crowd.generate"
    bl_label = "Generate Hina-dan"
    bl_description = "現在のパラメータで群衆(ひな壇配置)を新しい crowdN コレクションに生成する"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.crowd_animation_props
        if props.height_min_cm > props.height_max_cm:
            self.report({'ERROR'}, "Height Min が Max を超えています")
            return {'CANCELLED'}

        humans_2d, crowd_coll = build_hinadan(props)
        total = sum(len(r) for r in humans_2d)
        self.report({'INFO'}, f"{crowd_coll.name} に {total} 体生成しました")
        return {'FINISHED'}


class CROWD_OT_APPLY_WAVE(bpy.types.Operator):
    bl_idname = "crowd.apply_wave"
    bl_label = "Apply Wave Animation"
    bl_description = "選択した crowd コレクションに wave_remap を NLA で時間差適用する"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        wprops = context.scene.wave_animation_props
        coll = wprops.target_collection
        if coll is None:
            self.report({'ERROR'}, "Target Crowd コレクションを指定してください")
            return {'CANCELLED'}

        action = wprops.action
        if action is None:
            action = bpy.data.actions.get(DEFAULT_WAVE_ACTION_NAME)
        if action is None:
            self.report({'ERROR'},
                        f"Action 未指定で '{DEFAULT_WAVE_ACTION_NAME}' も見つかりません")
            return {'CANCELLED'}

        animator = WaveAnimator(
            coll, action,
            start_frame=wprops.start_frame,
            col_delay=wprops.col_delay,
            row_delay=wprops.row_delay,
        )
        n = animator.apply()
        if n == 0:
            self.report({'WARNING'},
                        f"{coll.name}: 対象リグが見つかりません(crowd_row/col の刻印がない?)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{coll.name}: {n} 体にウェーブを適用しました")
        return {'FINISHED'}


class CROWD_OT_OFFSET_COLLECTION(bpy.types.Operator):
    bl_idname = "crowd.offset_collection"
    bl_label = "Apply Offset"
    bl_description = "選択した crowd コレクション全体に XYZ オフセットを加算する(ボタンを押すたびに加算)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        oprops = context.scene.crowd_offset_props
        coll = oprops.target_collection
        if coll is None:
            self.report({'ERROR'}, "Target Crowd コレクションを指定してください")
            return {'CANCELLED'}

        dx, dy, dz = oprops.offset
        n = 0
        # 親のないオブジェクト(=リグ)だけ動かせば、子(ボディ等)は追従する
        for obj in coll.objects:
            if obj.parent is None:
                obj.location.x += dx
                obj.location.y += dy
                obj.location.z += dz
                n += 1

        self.report({'INFO'},
                    f"{coll.name}: {n} 体を ({dx:.2f}, {dy:.2f}, {dz:.2f}) オフセット")
        return {'FINISHED'}


# ============================================================
# パネル UI
# ============================================================
class CrowdAnimationPanel(bpy.types.Panel):
    bl_label = "Crowd Animation"
    bl_idname = "CROWD_PT_crowd_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Crowd Animation'

    def draw(self, context):
        layout = self.layout
        props = context.scene.crowd_animation_props

        layout.label(text="Generate Crowd Animation", icon='ANIM')

        box = layout.box()
        box.label(text="Layout", icon='MESH_GRID')
        box.prop(props, "cols")
        box.prop(props, "rows")
        box.prop(props, "col_spacing")
        box.prop(props, "row_spacing")
        box.prop(props, "step_height")

        box = layout.box()
        box.label(text="Height (cm)", icon='ARROW_LEFTRIGHT')
        row = box.row(align=True)
        row.prop(props, "height_min_cm")
        row.prop(props, "height_max_cm")

        box = layout.box()
        box.label(text="Random", icon='FILE_REFRESH')
        box.prop(props, "seed")

        layout.separator()
        layout.operator('crowd.generate', icon='OUTLINER_OB_ARMATURE')


class WaveAnimationPanel(bpy.types.Panel):
    bl_label = "Wave Animation"
    bl_idname = "CROWD_PT_wave_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Crowd Animation'

    def draw(self, context):
        layout = self.layout
        wprops = context.scene.wave_animation_props

        box = layout.box()
        box.label(text="Wave (NLA)", icon='FORCE_HARMONIC')
        box.prop(wprops, "target_collection")
        box.prop(wprops, "action")
        box.prop(wprops, "start_frame")
        box.prop(wprops, "col_delay")
        box.prop(wprops, "row_delay")

        layout.operator("crowd.apply_wave", icon='NLA')


class CrowdOffsetPanel(bpy.types.Panel):
    bl_label = "Crowd Offset"
    bl_idname = "CROWD_PT_crowd_offset"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Crowd Animation'

    def draw(self, context):
        layout = self.layout
        oprops = context.scene.crowd_offset_props

        box = layout.box()
        box.label(text="Offset Whole Collection", icon='ORIENTATION_GLOBAL')
        box.prop(oprops, "target_collection")
        box.prop(oprops, "offset")

        layout.operator("crowd.offset_collection", icon='TRANSFORM_ORIGINS')


# ============================================================
# 登録 / 解除
# ============================================================
classes = (
    CrowdAnimationProperties,
    WaveAnimationProperties,
    CrowdOffsetProperties,
    CROWD_OT_GENERATE,
    CROWD_OT_APPLY_WAVE,
    CROWD_OT_OFFSET_COLLECTION,
    CrowdAnimationPanel,
    WaveAnimationPanel,
    CrowdOffsetPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.crowd_animation_props = PointerProperty(type=CrowdAnimationProperties)
    bpy.types.Scene.wave_animation_props = PointerProperty(type=WaveAnimationProperties)
    bpy.types.Scene.crowd_offset_props = PointerProperty(type=CrowdOffsetProperties)


def unregister():
    del bpy.types.Scene.crowd_offset_props
    del bpy.types.Scene.wave_animation_props
    del bpy.types.Scene.crowd_animation_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
