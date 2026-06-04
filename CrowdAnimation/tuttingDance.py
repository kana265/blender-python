bl_info = {
    "name": "Generate Tutting Dance Crowd",
    "description": "blend ファイル内の20体モデル(BOY_1..10 / GIRL_1..10)をひな壇配置し、NLA でウェーブ(タッティング)を付けるアドオン",
    "author": "Authors name",
    "version": (0, 1, 0),
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
import random
from bpy.props import (
    IntProperty,
    FloatProperty,
    PointerProperty,
    FloatVectorProperty,
    EnumProperty,
)


# ============================================================
# 定数
# ============================================================
# blend に用意されているモデルコレクション群。
#   ("BOY", 10)  -> BOY_1, BOY_2, ... BOY_10
#   ("GIRL", 10) -> GIRL_1, GIRL_2, ... GIRL_10
MODEL_GROUPS = [("BOY", 10), ("GIRL", 10)]

# NLA に積むトラック名(ウェーブ専用)
WAVE_TRACK_NAME = "WaveAnimation"

# 既定のウェーブアクション名(Action ピッカーが未設定のときの自動フォールバック)
DEFAULT_WAVE_ACTION_NAME = "tutting"

# リグに刻むカスタムプロパティ名
PROP_ROW = "crowd_row"
PROP_COL = "crowd_col"
PROP_HOME = "crowd_home"  # 整列直後の基準位置 [x, y, z]


# ============================================================
# オフセット即時追従用コールバック
# ============================================================
# update コールバックは property 定義より前に参照できる必要があるため、
# PropertyGroup クラス定義の前にモジュール関数として置く。
def _on_offset_update(self, context):
    """offset スライダーが動くたびに呼ばれ、刻印付き全リグを home + offset へ移動する。

    home 基準の絶対オフセットなので何度呼ばれても idempotent。ドラッグ追従できる。
    """
    ox, oy, oz = self.offset
    for rig in collect_crowd_rigs():
        home = rig.get(PROP_HOME)
        if home is None:
            continue
        rig.location = (home[0] + ox, home[1] + oy, home[2] + oz)


# ============================================================
# PropertyGroup: ひな壇配置パラメータ
# ============================================================
class CrowdAnimationProperties(bpy.types.PropertyGroup):
    cols: IntProperty(
        name="Columns",
        description="横方向(X)に並べる人数",
        default=5, min=1, max=20,
    )
    rows: IntProperty(
        name="Rows (Steps)",
        description="段数(Y方向)。奥に行くほど高くなる",
        default=4, min=1, max=20,
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
    seed: IntProperty(
        name="Seed",
        description="0 = 完全ランダム / 正の整数 = その値で固定して並びを再現可能",
        default=0, min=0,
    )


# ============================================================
# PropertyGroup: ウェーブアニメーションのパラメータ
# ============================================================
class WaveAnimationProperties(bpy.types.PropertyGroup):
    wave_type: EnumProperty(
        name="Wave Type",
        description="波の伝搬方向",
        items=[
            ('LEFT', "From Left", "左端(col=0)から右へ波が伝搬する"),
            ('RIGHT', "From Right", "右端(col=cols-1)から左へ波が伝搬する"),
            ('DIAGONAL', "Diagonal", "行番号+列番号が等しい反対角線ごとに波が伝搬する"),
        ],
        default='LEFT',
    )
    action: PointerProperty(
        name="Wave Action",
        description="NLA に積むアクション。未指定なら 'tutting' を自動で探す",
        type=bpy.types.Action,
    )
    start_frame: IntProperty(
        name="Start Frame",
        description="波の先頭が再生開始するフレーム",
        default=1, min=0,
    )
    col_delay: FloatProperty(
        name="Step Delay",
        description="1ステップ(列 / 反対角線)ぶんずらすフレーム数",
        default=3.0, min=0.0,
    )
    row_delay: FloatProperty(
        name="Row Delay",
        description="1段(奥行き)ぶんずらすフレーム数。From Left/Right のときのみ加味",
        default=0.0,
    )


# ============================================================
# PropertyGroup: コレクション単位のオフセット(即時追従)
# ============================================================
class CrowdOffsetProperties(bpy.types.PropertyGroup):
    offset: FloatVectorProperty(
        name="Offset",
        description="群衆全体に加える XYZ オフセット [m]。スライダーを動かすと即追従する",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype='TRANSLATION',
        update=_on_offset_update,
    )


# ============================================================
# モデルコレクション系ヘルパ
# ============================================================
def iter_model_collection_names():
    """MODEL_GROUPS から "BOY_1" .. "GIRL_10" の名前を順に生成する。"""
    for prefix, count in MODEL_GROUPS:
        for i in range(1, count + 1):
            yield f"{prefix}_{i}"


def discover_model_collections() -> list[bpy.types.Collection]:
    """blend に実在するモデルコレクションだけを list で返す。"""
    colls = []
    for name in iter_model_collection_names():
        coll = bpy.data.collections.get(name)
        if coll is not None:
            colls.append(coll)
    return colls


def get_collection_rig(coll: bpy.types.Collection) -> bpy.types.Object | None:
    """コレクション内の最初の Armature オブジェクトを返す(無ければ None)。"""
    for obj in coll.objects:
        if obj.type == 'ARMATURE':
            return obj
    return None


# ============================================================
# ひな壇配置
# ============================================================
def arrange_hinadan(props: "CrowdAnimationProperties") -> int:
    """blend 内のモデルコレクションのリグをひな壇グリッドに整列する。配置した体数を返す。

    - seed 付きでリグ一覧をシャッフルし、rows×cols のセルへ row-major に割り当てる。
    - 割当数は min(rows*cols, 利用可能なリグ数)。余ったセルは空、余ったモデルは未使用。
    - 各リグに crowd_row / crowd_col と home 位置を刻む(ウェーブ・オフセットの基準)。
    """
    if props.seed != 0:
        random.seed(props.seed)

    rigs = []
    for coll in discover_model_collections():
        rig = get_collection_rig(coll)
        if rig is not None:
            rigs.append(rig)

    if not rigs:
        return 0

    random.shuffle(rigs)

    col_offset = -(props.cols - 1) / 2.0 * props.col_spacing
    placed = 0
    rig_iter = iter(rigs)

    for row in range(props.rows):
        y = row * props.row_spacing
        z = row * props.step_height
        for col in range(props.cols):
            rig = next(rig_iter, None)
            if rig is None:
                # 使えるモデルを使い切った
                print(f"[Crowd] Done. {placed} rigs placed.")
                return placed

            x = col_offset + col * props.col_spacing

            print(f"[Crowd] {rig.name} row={row+1}/{props.rows} "
                  f"col={col+1}/{props.cols} -> ({x:.2f}, {y:.2f}, {z:.2f})")

            rig.location = (x, y, z)
            rig[PROP_ROW] = row
            rig[PROP_COL] = col
            rig[PROP_HOME] = [x, y, z]
            placed += 1

    print(f"[Crowd] Done. {placed} rigs placed.")
    return placed


# ============================================================
# リグ収集 / グリッド再構築
# ============================================================
def collect_crowd_rigs() -> list[bpy.types.Object]:
    """モデルコレクション群から crowd_row/crowd_col 刻印付きの Armature を集める。"""
    rigs = []
    for coll in discover_model_collections():
        rig = get_collection_rig(coll)
        if rig is None:
            continue
        if PROP_ROW in rig.keys() and PROP_COL in rig.keys():
            rigs.append(rig)
    return rigs


def build_grid(rigs: list[bpy.types.Object]):
    """刻印付きリグ一覧を (row, col) インデックスから 2次元配列に並べ直す。"""
    if not rigs:
        return []

    rows = max(int(r[PROP_ROW]) for r in rigs) + 1
    cols = max(int(r[PROP_COL]) for r in rigs) + 1

    grid: list[list[bpy.types.Object | None]] = [[None] * cols for _ in range(rows)]
    for r in rigs:
        grid[int(r[PROP_ROW])][int(r[PROP_COL])] = r
    return grid


# ============================================================
# ウェーブアニメーション
# ============================================================
class WaveAnimator:
    """刻印付きリグに対し、アクションを時間差付きで NLA に積むクラス。

    挙動:
      - 各リグの NLA に WAVE_TRACK_NAME という名前のトラックを用意
      - wave_type に応じた遅延フレームだけ後ろにずらして strip を貼る
        - LEFT     : col*col_delay + row*row_delay
        - RIGHT    : (cols-1-col)*col_delay + row*row_delay
        - DIAGONAL : (row+col)*col_delay  (row+col が等しい反対角線が同時に動く)
      - 同名 strip は一度消してから貼り直す(再適用しやすい)
    """

    def __init__(
        self,
        action: bpy.types.Action,
        wave_type: str = 'LEFT',
        start_frame: int = 1,
        col_delay: float = 3.0,
        row_delay: float = 0.0,
        track_name: str = WAVE_TRACK_NAME,
    ):
        self.action = action
        self.wave_type = wave_type
        self.start_frame = int(start_frame)
        self.col_delay = float(col_delay)
        self.row_delay = float(row_delay)
        self.track_name = track_name

    def _delay_frames(self, row: int, col: int, cols: int) -> int:
        if self.wave_type == 'RIGHT':
            offset = (cols - 1 - col) * self.col_delay + row * self.row_delay
        elif self.wave_type == 'DIAGONAL':
            offset = (row + col) * self.col_delay
        else:  # 'LEFT'
            offset = col * self.col_delay + row * self.row_delay
        return int(round(offset))

    def _ensure_track(self, rig: bpy.types.Object) -> bpy.types.NlaTrack:
        if rig.animation_data is None:
            rig.animation_data_create()
        ad = rig.animation_data
        track = ad.nla_tracks.get(self.track_name)
        if track is None:
            track = ad.nla_tracks.new()
            track.name = self.track_name
        return track

    def _add_strip(self, rig: bpy.types.Object, row: int, col: int, cols: int):
        start = self.start_frame + self._delay_frames(row, col, cols)

        track = self._ensure_track(rig)
        strip_name = f"wave_r{row}_c{col}"
        # 既存の同名 strip を消してから貼り直す(再適用しやすいように)
        for s in list(track.strips):
            if s.name == strip_name:
                track.strips.remove(s)
        return track.strips.new(strip_name, start, self.action)

    def apply(self) -> int:
        grid = build_grid(collect_crowd_rigs())
        count = 0
        for row, row_rigs in enumerate(grid):
            cols = len(row_rigs)
            for col, rig in enumerate(row_rigs):
                if rig is None:
                    continue
                self._add_strip(rig, row, col, cols)
                count += 1
        return count


# ============================================================
# オペレータ
# ============================================================
class CROWD_OT_ARRANGE(bpy.types.Operator):
    bl_idname = "crowd.arrange"
    bl_label = "Arrange Hina-dan"
    bl_description = "blend 内のモデル(BOY_*/GIRL_*)をひな壇グリッドに整列する"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.crowd_animation_props
        n = arrange_hinadan(props)
        if n == 0:
            self.report({'WARNING'},
                        "モデルコレクション(BOY_*/GIRL_*)が見つからないか、Armature がありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} 体をひな壇に整列しました")
        return {'FINISHED'}


class CROWD_OT_APPLY_WAVE(bpy.types.Operator):
    bl_idname = "crowd.apply_wave"
    bl_label = "Apply Wave Animation"
    bl_description = "整列済みの群衆に指定アクションを NLA で時間差適用する"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        wprops = context.scene.wave_animation_props

        action = wprops.action
        if action is None:
            action = bpy.data.actions.get(DEFAULT_WAVE_ACTION_NAME)
        if action is None:
            self.report({'ERROR'},
                        f"Action 未指定で '{DEFAULT_WAVE_ACTION_NAME}' も見つかりません")
            return {'CANCELLED'}

        animator = WaveAnimator(
            action,
            wave_type=wprops.wave_type,
            start_frame=wprops.start_frame,
            col_delay=wprops.col_delay,
            row_delay=wprops.row_delay,
        )
        n = animator.apply()
        if n == 0:
            self.report({'WARNING'},
                        "対象リグが見つかりません(先に Arrange Hina-dan を実行してください)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} 体に {wprops.wave_type} ウェーブを適用しました")
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

        layout.label(text="Arrange Crowd (Hina-dan)", icon='ANIM')

        box = layout.box()
        box.label(text="Layout", icon='MESH_GRID')
        box.prop(props, "cols")
        box.prop(props, "rows")
        box.prop(props, "col_spacing")
        box.prop(props, "row_spacing")
        box.prop(props, "step_height")

        box = layout.box()
        box.label(text="Random", icon='FILE_REFRESH')
        box.prop(props, "seed")

        layout.separator()
        layout.operator('crowd.arrange', icon='OUTLINER_OB_ARMATURE')


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
        box.prop(wprops, "wave_type")
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
        box.label(text="Offset Whole Crowd", icon='ORIENTATION_GLOBAL')
        box.label(text="スライダーを動かすと即追従", icon='INFO')
        box.prop(oprops, "offset")


# ============================================================
# 登録 / 解除
# ============================================================
classes = (
    CrowdAnimationProperties,
    WaveAnimationProperties,
    CrowdOffsetProperties,
    CROWD_OT_ARRANGE,
    CROWD_OT_APPLY_WAVE,
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
