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
import math
import random
from mathutils import Euler
from bpy.props import (
    IntProperty,
    FloatProperty,
    StringProperty,
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

# 腕の傾きに個体差を出す加算レイヤー用のトラック名 / 生成アクションの接頭辞
ARM_TRACK_NAME = "ArmVariation"
ARM_ACTION_PREFIX = "armvar_"

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
        description="波の伝搬パターン",
        items=[
            ('LEFT', "From Left", "左端(col=0)から右へ波が伝搬する"),
            ('RIGHT', "From Right", "右端(col=cols-1)から左へ波が伝搬する"),
            ('TOP_LEFT', "From Top-Left", "左奥コーナー(row最大, col=0)から外側へ波が伝搬する"),
            ('TOP_RIGHT', "From Top-Right", "右奥コーナー(row最大, col最大)から外側へ波が伝搬する"),
            ('COL_PARITY', "Odd/Even Columns", "偶数列が同時、奇数列が同時(2フェーズで交互)"),
            ('ROW_PARITY', "Odd/Even Rows", "偶数段が同時、奇数段が同時(2フェーズで交互)"),
            ('DIAGONAL', "Diagonal Parity", "row+col が同じ反対角線が同時。さらに偶数/奇数の反対角線が同時(対角線ごとに交互)"),
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
    timing_jitter: FloatProperty(
        name="Timing Jitter",
        description="各リグの開始フレームに加える ±ランダム量 [フレーム]。"
                    "リズムが崩れない小さめの値(1拍未満)を推奨。波の機械的な均一さを崩す",
        default=0.0, min=0.0, soft_max=5.0,
    )
    speed_jitter: FloatProperty(
        name="Speed Jitter",
        description="各リグの再生速度(NLA strip scale)に加える ±ランダム割合。"
                    "動きの速さに個体差を出す(0.05 = ±5%)。大きいと拍からズレる",
        default=0.0, min=0.0, max=0.5,
    )
    jitter_seed: IntProperty(
        name="Jitter Seed",
        description="ノイズ(timing / speed / phase / arm)の再現用シード。値を変えると揺らぎ方が変わる",
        default=1, min=0,
    )
    # --- 位相ずらし(拍量子化): 各リグを拍単位でずらし、同じ瞬間に別のポーズにする ---
    phase_jitter_beats: IntProperty(
        name="Phase Jitter (beats)",
        description="各リグの再生位相を 0〜N 拍ぶんランダムにずらす。拍単位なのでリズムは崩れない。"
                    "ループするアクションだと『全員が踊りの別の箇所にいる』状態になり均一さが消える",
        default=0, min=0, soft_max=8,
    )
    beat_frames: FloatProperty(
        name="Frames / Beat",
        description="1拍ぶんのフレーム数。Phase Jitter の量子化単位(例: 60fps・120BPM なら 30)",
        default=8.0, min=1.0,
    )
    # --- 腕の傾きの個体差(加算 NLA レイヤー) ---
    arm_tilt_deg: FloatProperty(
        name="Arm Tilt Variation",
        description="腕系ボーンに加える ±ランダム回転の最大角度 [度]。0 で無効。"
                    "踊りを保ったまま各リグの腕の傾きだけ変える(加算レイヤー)",
        default=0.0, min=0.0, soft_max=20.0,
    )
    arm_bones: StringProperty(
        name="Arm Bone Filter",
        description="対象ボーン名のフィルタ(カンマ区切り・部分一致・大小無視)。"
                    "リグの命名に合わせて調整する",
        default="arm,shoulder,forearm,hand",
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
        - LEFT       : col*col_delay + row*row_delay
        - RIGHT      : (cols-1-col)*col_delay + row*row_delay
        - TOP_LEFT   : ((rows-1-row) + col)*col_delay        (左奥コーナーからの距離)
        - TOP_RIGHT  : ((rows-1-row) + (cols-1-col))*col_delay (右奥コーナーからの距離)
        - COL_PARITY : (col % 2)*col_delay                   (偶数列/奇数列の2フェーズ)
        - ROW_PARITY : (row % 2)*col_delay                   (偶数段/奇数段の2フェーズ)
        - DIAGONAL   : ((row+col) % 2)*col_delay             (反対角線が偶奇2フェーズで交互)
      - 同名 strip は一度消してから貼り直す(再適用しやすい)
    """

    def __init__(
        self,
        action: bpy.types.Action,
        wave_type: str = 'LEFT',
        start_frame: int = 1,
        col_delay: float = 3.0,
        row_delay: float = 0.0,
        timing_jitter: float = 0.0,
        speed_jitter: float = 0.0,
        phase_jitter_beats: int = 0,
        beat_frames: float = 8.0,
        jitter_seed: int = 1,
        track_name: str = WAVE_TRACK_NAME,
    ):
        self.action = action
        self.wave_type = wave_type
        self.start_frame = int(start_frame)
        self.col_delay = float(col_delay)
        self.row_delay = float(row_delay)
        self.timing_jitter = float(timing_jitter)
        self.speed_jitter = float(speed_jitter)
        self.phase_jitter_beats = int(phase_jitter_beats)
        self.beat_frames = float(beat_frames)
        self.jitter_seed = int(jitter_seed)
        self.track_name = track_name

    def _rig_rng(self, row: int, col: int) -> random.Random:
        """(row, col) ごとに決定的な乱数発生器。再適用しても同じ揺らぎを再現する。"""
        return random.Random(self.jitter_seed * 1000003 + row * 1009 + col)

    def _delay_frames(self, row: int, col: int, rows: int, cols: int) -> int:
        if self.wave_type == 'RIGHT':
            offset = (cols - 1 - col) * self.col_delay + row * self.row_delay
        elif self.wave_type == 'TOP_LEFT':
            offset = ((rows - 1 - row) + col) * self.col_delay
        elif self.wave_type == 'TOP_RIGHT':
            offset = ((rows - 1 - row) + (cols - 1 - col)) * self.col_delay
        elif self.wave_type == 'COL_PARITY':
            offset = (col % 2) * self.col_delay
        elif self.wave_type == 'ROW_PARITY':
            offset = (row % 2) * self.col_delay
        elif self.wave_type == 'DIAGONAL':
            offset = ((row + col) % 2) * self.col_delay
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

    def _add_strip(self, rig: bpy.types.Object, row: int, col: int, rows: int, cols: int):
        rng = self._rig_rng(row, col)

        start = self.start_frame + self._delay_frames(row, col, rows, cols)
        # 位相ずらし: 0〜N 拍ぶんを拍単位でずらす(リズムを保ったまま位相を散らす)
        if self.phase_jitter_beats > 0 and self.beat_frames > 0.0:
            beats = rng.randint(0, self.phase_jitter_beats)
            start += int(round(beats * self.beat_frames))
        # タイミングノイズ: ±timing_jitter フレームの揺らぎ(機械的な均一さを崩す)
        if self.timing_jitter > 0.0:
            start += int(round(rng.uniform(-self.timing_jitter, self.timing_jitter)))
        start = max(start, 0)

        track = self._ensure_track(rig)
        strip_name = f"wave_r{row}_c{col}"
        # 既存の同名 strip を消してから貼り直す(再適用しやすいように)
        for s in list(track.strips):
            if s.name == strip_name:
                track.strips.remove(s)
        strip = track.strips.new(strip_name, start, self.action)

        # 速度ノイズ: 再生スピードに個体差をつける(動きの速さを揃えすぎない)
        if self.speed_jitter > 0.0:
            strip.scale = 1.0 + rng.uniform(-self.speed_jitter, self.speed_jitter)

        return strip

    def apply(self) -> int:
        grid = build_grid(collect_crowd_rigs())
        rows = len(grid)
        count = 0
        for row, row_rigs in enumerate(grid):
            cols = len(row_rigs)
            for col, rig in enumerate(row_rigs):
                if rig is None:
                    continue
                self._add_strip(rig, row, col, rows, cols)
                count += 1
        return count

    @staticmethod
    def remove(track_name: str = WAVE_TRACK_NAME) -> int:
        """全リグから WAVE_TRACK_NAME トラック(と配下の strip)を削除する。削除した体数を返す。"""
        count = 0
        for rig in collect_crowd_rigs():
            ad = rig.animation_data
            if ad is None:
                continue
            track = ad.nla_tracks.get(track_name)
            if track is not None:
                ad.nla_tracks.remove(track)
                count += 1
        return count


# ============================================================
# 腕の傾きの個体差(加算 NLA レイヤー)
# ============================================================
def _arm_target_bones(rig: bpy.types.Object, tokens: list[str]) -> list:
    """名前に tokens のいずれかを含むポーズボーンを返す(大小無視・部分一致)。"""
    if not tokens:
        return []
    return [pb for pb in rig.pose.bones
            if any(t in pb.name.lower() for t in tokens)]


def clear_arm_variation(rig: bpy.types.Object):
    """リグから ArmVariation トラックを外し、専用に生成したアクションを掃除する。"""
    ad = rig.animation_data
    if ad is None:
        return
    track = ad.nla_tracks.get(ARM_TRACK_NAME)
    if track is None:
        return
    acts = [s.action for s in track.strips if s.action is not None]
    ad.nla_tracks.remove(track)
    for a in acts:
        if a.users == 0 and a.name.startswith(ARM_ACTION_PREFIX):
            try:
                bpy.data.actions.remove(a)
            except Exception:
                pass


def apply_arm_variation(tilt_deg: float, bone_filter: str, seed: int) -> int:
    """各リグに、腕系ボーンを微小回転させる加算レイヤーを重ねる。適用した体数を返す。

    - 踊り本体(WaveAnimation)はそのまま、上に blend_type='ADD' の1キーアクションを重ねる。
    - ボーンの rotation_mode に合わせて euler / quaternion で値を入れる。
    - (row, col) ごとに決定的な乱数を使い、再適用で同じ傾きを再現する。
    """
    tokens = [t.strip().lower() for t in bone_filter.split(",") if t.strip()]
    amp = math.radians(tilt_deg)
    count = 0

    for rig in collect_crowd_rigs():
        # 既存の腕レイヤーを作り直す(再適用しやすいように)
        clear_arm_variation(rig)
        if amp <= 0.0:
            continue

        bones = _arm_target_bones(rig, tokens)
        if not bones:
            continue

        row = int(rig[PROP_ROW])
        col = int(rig[PROP_COL])
        rng = random.Random(seed * 7919 + row * 131 + col + 17)

        act = bpy.data.actions.new(f"{ARM_ACTION_PREFIX}{rig.name}")
        for pb in bones:
            rx = rng.uniform(-amp, amp)
            ry = rng.uniform(-amp, amp)
            rz = rng.uniform(-amp, amp)
            if pb.rotation_mode == 'QUATERNION':
                q = Euler((rx, ry, rz), 'XYZ').to_quaternion()
                path = f'pose.bones["{pb.name}"].rotation_quaternion'
                for i, v in enumerate((q.w, q.x, q.y, q.z)):
                    fc = act.fcurves.new(path, index=i)
                    fc.keyframe_points.insert(1.0, v)
            else:
                path = f'pose.bones["{pb.name}"].rotation_euler'
                for i, v in enumerate((rx, ry, rz)):
                    fc = act.fcurves.new(path, index=i)
                    fc.keyframe_points.insert(1.0, v)

        if rig.animation_data is None:
            rig.animation_data_create()
        ad = rig.animation_data
        # ADD トラックは後から追加するとスタック最上段=波の上に乗る
        track = ad.nla_tracks.new()
        track.name = ARM_TRACK_NAME
        strip = track.strips.new("armvar", 1, act)
        strip.blend_type = 'ADD'
        strip.extrapolation = 'HOLD'  # 1キーを全フレームに適用
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
            timing_jitter=wprops.timing_jitter,
            speed_jitter=wprops.speed_jitter,
            phase_jitter_beats=wprops.phase_jitter_beats,
            beat_frames=wprops.beat_frames,
            jitter_seed=wprops.jitter_seed,
        )
        n = animator.apply()
        if n == 0:
            self.report({'WARNING'},
                        "対象リグが見つかりません(先に Arrange Hina-dan を実行してください)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} 体に {wprops.wave_type} ウェーブを適用しました")
        return {'FINISHED'}


class CROWD_OT_REMOVE_WAVE(bpy.types.Operator):
    bl_idname = "crowd.remove_wave"
    bl_label = "Remove Wave Animation"
    bl_description = "適用済みのウェーブと腕バリエーション(NLA トラック)を全リグから取り除く"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = WaveAnimator.remove()
        for rig in collect_crowd_rigs():
            clear_arm_variation(rig)
        if n == 0:
            self.report({'WARNING'}, "取り除くウェーブが見つかりません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} 体からウェーブを取り除きました")
        return {'FINISHED'}


class CROWD_OT_APPLY_ARM_VARIATION(bpy.types.Operator):
    bl_idname = "crowd.apply_arm_variation"
    bl_label = "Apply Arm Variation"
    bl_description = "腕系ボーンに個体差(微小回転)を加える加算レイヤーを全リグに重ねる"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        wprops = context.scene.wave_animation_props
        if wprops.arm_tilt_deg <= 0.0:
            self.report({'ERROR'}, "Arm Tilt Variation を 0 より大きくしてください")
            return {'CANCELLED'}

        n = apply_arm_variation(
            wprops.arm_tilt_deg,
            wprops.arm_bones,
            wprops.jitter_seed,
        )
        if n == 0:
            self.report({'WARNING'},
                        "対象リグ/ボーンが見つかりません(Arrange 済みか、Bone Filter を確認)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} 体に腕バリエーションを適用しました")
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

        jbox = layout.box()
        jbox.label(text="Noise / Variation", icon='MOD_NOISE')
        jbox.prop(wprops, "timing_jitter")
        jbox.prop(wprops, "speed_jitter")
        jbox.separator()
        jbox.prop(wprops, "phase_jitter_beats")
        jbox.prop(wprops, "beat_frames")
        jbox.separator()
        jbox.prop(wprops, "jitter_seed")

        row = layout.row(align=True)
        row.operator("crowd.apply_wave", icon='NLA')
        row.operator("crowd.remove_wave", icon='TRASH')

        abox = layout.box()
        abox.label(text="Arm Variation (additive)", icon='BONE_DATA')
        abox.prop(wprops, "arm_tilt_deg")
        abox.prop(wprops, "arm_bones")
        abox.operator("crowd.apply_arm_variation", icon='CONSTRAINT_BONE')


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
    CROWD_OT_REMOVE_WAVE,
    CROWD_OT_APPLY_ARM_VARIATION,
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
