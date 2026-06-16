# SPDX-License-Identifier: MIT
"""MIDI Drum Lights — MIDIファイルのドラムに同期してライトの強度・色をキーフレームにベイクするアドオン。

REAPER等で作成したドラムMIDI(例: tuttingdance_test.mid)を読み込み、
- キック(note 36) -> ライトの強度(energy)をパルス発光
- クラッシュ(note 49) -> ライトの色(color)をパレット順に切替
として、シーンFPSに合わせたキーフレームを焼き込む。外部依存なし(純PythonのSMFパーサ内蔵)。
"""

bl_info = {
    "name": "MIDI Drum Lights",
    "author": "Tutting Dance",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > サイドバー(N) > MIDI Lights",
    "description": "MIDIドラムに同期してライトの強度・色をキーフレームにベイクする",
    "category": "Animation",
}

import struct

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList


# ---------------------------------------------------------------------------
# MIDI パーサ(Standard MIDI File, 外部依存なし)
# ---------------------------------------------------------------------------

class MidiEvent:
    """note-on イベント。time_sec は曲頭からの秒数。"""

    __slots__ = ("time_sec", "channel", "note", "velocity")

    def __init__(self, time_sec, channel, note, velocity):
        self.time_sec = time_sec
        self.channel = channel
        self.note = note
        self.velocity = velocity


def _read_vlq(data, pos):
    """可変長数値(variable-length quantity)を読む。(値, 次の位置) を返す。"""
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, pos


def parse_midi(path):
    """MIDIファイルを解析し note-on イベントのリスト(time_sec昇順)を返す。

    テンポマップ(FF 51)に対応して tick->秒 を区分線形変換する。
    """
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:4] != b"MThd":
        raise ValueError("MThd ヘッダが見つかりません。MIDIファイルではない可能性があります。")

    header_len = struct.unpack(">I", data[4:8])[0]
    _fmt, _ntrk, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTEタイムコード形式のMIDIは未対応です(TPQN形式のみ対応)。")
    ticks_per_quarter = division
    pos = 8 + header_len

    # (1) 全トラックを走査し、絶対tick付きで「note-onイベント」と「テンポ変更」を収集する。
    raw_notes = []  # (abs_tick, channel, note, velocity)
    tempo_changes = []  # (abs_tick, us_per_quarter)

    while pos + 8 <= len(data):
        if data[pos:pos + 4] != b"MTrk":
            break
        track_len = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        pos += 8
        end = pos + track_len
        p = pos
        abs_tick = 0
        running_status = None

        while p < end:
            delta, p = _read_vlq(data, p)
            abs_tick += delta
            status = data[p]
            if status & 0x80:
                running_status = status
                p += 1
            else:
                status = running_status  # ランニングステータス
            if status is None:
                break

            if status == 0xFF:  # メタイベント
                meta_type = data[p]
                p += 1
                length, p = _read_vlq(data, p)
                payload = data[p:p + length]
                p += length
                if meta_type == 0x51 and length == 3:
                    tempo_changes.append((abs_tick, struct.unpack(">I", b"\x00" + payload)[0]))
            elif status in (0xF0, 0xF7):  # SysEx
                length, p = _read_vlq(data, p)
                p += length
            else:
                high = status & 0xF0
                channel = status & 0x0F
                if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):  # データ2バイト
                    d1 = data[p]
                    d2 = data[p + 1]
                    p += 2
                    if high == 0x90 and d2 > 0:  # note-on(velocity>0)
                        raw_notes.append((abs_tick, channel, d1, d2))
                elif high in (0xC0, 0xD0):  # データ1バイト
                    p += 1
                else:
                    break  # 不明なステータス: トラックを打ち切り
        pos = end

    # (2) テンポマップを構築(tick順)。先頭にデフォルト120BPMを保証。
    tempo_changes.sort(key=lambda t: t[0])
    if not tempo_changes or tempo_changes[0][0] > 0:
        tempo_changes.insert(0, (0, 500000))

    def tick_to_seconds(target_tick):
        seconds = 0.0
        for i, (t_tick, us_per_q) in enumerate(tempo_changes):
            next_tick = tempo_changes[i + 1][0] if i + 1 < len(tempo_changes) else None
            if next_tick is not None and next_tick <= target_tick:
                span = next_tick - t_tick
            else:
                span = target_tick - t_tick
            if span > 0:
                seconds += span * (us_per_q / 1_000_000.0) / ticks_per_quarter
            if next_tick is None or next_tick > target_tick:
                break
        return seconds

    events = [
        MidiEvent(tick_to_seconds(tick), ch, note, vel)
        for tick, ch, note, vel in raw_notes
    ]
    events.sort(key=lambda e: e.time_sec)
    return events


# ---------------------------------------------------------------------------
# プロパティ
# ---------------------------------------------------------------------------

def _light_object_poll(self, obj):
    return obj.type == "LIGHT"


class MDL_LightItem(PropertyGroup):
    obj: PointerProperty(
        name="Light",
        type=bpy.types.Object,
        poll=_light_object_poll,
    )
    role: EnumProperty(
        name="役割",
        items=[
            ("INTENSITY", "強度", "キックで強度(energy)を変化"),
            ("COLOR", "色", "クラッシュで色(color)を変化"),
            ("BOTH", "両方", "強度と色の両方を変化"),
        ],
        default="BOTH",
    )


class MDL_PaletteColor(PropertyGroup):
    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
    )


class MDL_Settings(PropertyGroup):
    midi_path: StringProperty(
        name="MIDIファイル",
        subtype="FILE_PATH",
        default="//tuttingdance_test.mid",
    )
    lights: CollectionProperty(type=MDL_LightItem)
    lights_index: IntProperty(default=0)
    palette: CollectionProperty(type=MDL_PaletteColor)
    palette_index: IntProperty(default=0)

    kick_note: IntProperty(name="キックnote", default=36, min=0, max=127)
    crash_note: IntProperty(name="クラッシュnote", default=49, min=0, max=127)
    channel_filter: IntProperty(
        name="チャンネル",
        description="対象MIDIチャンネル(0開始)。-1で全チャンネル",
        default=0,
        min=-1,
        max=15,
    )

    start_frame: IntProperty(name="開始フレーム", default=1)

    base_energy: FloatProperty(name="基準強度", default=10.0, min=0.0)
    peak_energy: FloatProperty(name="ピーク強度", default=1000.0, min=0.0)
    attack_frames: IntProperty(name="アタック(F)", default=1, min=0)
    decay_frames: IntProperty(name="ディケイ(F)", default=6, min=1)
    use_velocity: BoolProperty(
        name="ベロシティでスケール",
        description="ピーク強度を note のベロシティ(0-127)でスケールする",
        default=True,
    )

    clear_existing: BoolProperty(
        name="既存キーを削除してからベイク",
        default=True,
    )


# ---------------------------------------------------------------------------
# UIList
# ---------------------------------------------------------------------------

class MDL_UL_lights(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "obj", text="", icon="LIGHT")
        row.prop(item, "role", text="")


class MDL_UL_palette(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")


# ---------------------------------------------------------------------------
# Operator: リスト操作
# ---------------------------------------------------------------------------

class MDL_OT_light_add(Operator):
    bl_idname = "mdl.light_add"
    bl_label = "ライト追加"
    bl_description = "選択中のライト(なければ空欄)をリストに追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.mdl_settings
        item = settings.lights.add()
        active = context.active_object
        if active and active.type == "LIGHT":
            item.obj = active
        settings.lights_index = len(settings.lights) - 1
        return {"FINISHED"}


class MDL_OT_light_remove(Operator):
    bl_idname = "mdl.light_remove"
    bl_label = "ライト削除"
    bl_description = "選択中の行をリストから削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.mdl_settings
        idx = settings.lights_index
        if 0 <= idx < len(settings.lights):
            settings.lights.remove(idx)
            settings.lights_index = min(idx, len(settings.lights) - 1)
        return {"FINISHED"}


class MDL_OT_palette_add(Operator):
    bl_idname = "mdl.palette_add"
    bl_label = "色追加"
    bl_description = "パレットに色を追加"
    bl_options = {"REGISTER", "UNDO"}

    # 追加時に巡回しやすい既定色
    _defaults = [
        (1.0, 0.1, 0.1), (0.1, 1.0, 0.1), (0.1, 0.3, 1.0),
        (1.0, 1.0, 0.1), (1.0, 0.1, 1.0), (0.1, 1.0, 1.0),
    ]

    def execute(self, context):
        settings = context.scene.mdl_settings
        item = settings.palette.add()
        item.color = self._defaults[len(settings.palette) % len(self._defaults) - 1]
        settings.palette_index = len(settings.palette) - 1
        return {"FINISHED"}


class MDL_OT_palette_remove(Operator):
    bl_idname = "mdl.palette_remove"
    bl_label = "色削除"
    bl_description = "選択中の色をパレットから削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.mdl_settings
        idx = settings.palette_index
        if 0 <= idx < len(settings.palette):
            settings.palette.remove(idx)
            settings.palette_index = min(idx, len(settings.palette) - 1)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operator: ベイク / クリア
# ---------------------------------------------------------------------------

def _iter_target_lights(settings):
    """(light_data, role) を返すジェネレータ。重複データブロックは許容(役割が別なら別行)。"""
    for item in settings.lights:
        if item.obj is not None and item.obj.type == "LIGHT":
            yield item.obj.data, item.role


def _clear_keys(light_data, data_path):
    anim = light_data.animation_data
    if not anim or not anim.action:
        return
    fcurves = anim.action.fcurves
    for fc in list(fcurves):
        if fc.data_path == data_path:
            fcurves.remove(fc)


def _set_interpolation(light_data, data_path, mode):
    anim = light_data.animation_data
    if not anim or not anim.action:
        return
    for fc in anim.action.fcurves:
        if fc.data_path == data_path:
            for kp in fc.keyframe_points:
                kp.interpolation = mode
            fc.update()


class MDL_OT_bake(Operator):
    bl_idname = "mdl.bake"
    bl_label = "ベイク"
    bl_description = "MIDIを解析してライトの強度・色にキーフレームを焼き込む"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.mdl_settings

        if len(settings.lights) == 0:
            self.report({"ERROR"}, "ライトが登録されていません。")
            return {"CANCELLED"}

        path = bpy.path.abspath(settings.midi_path)
        try:
            events = parse_midi(path)
        except FileNotFoundError:
            self.report({"ERROR"}, f"MIDIファイルが見つかりません: {path}")
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001 - ユーザーに原因を通知
            self.report({"ERROR"}, f"MIDI解析に失敗: {exc}")
            return {"CANCELLED"}

        # 色担当が居る場合はパレット必須
        needs_color = any(role in ("COLOR", "BOTH") for _ld, role in _iter_target_lights(settings))
        if needs_color and len(settings.palette) == 0:
            self.report({"ERROR"}, "色担当ライトがありますが、パレットが空です。")
            return {"CANCELLED"}

        fps = scene.render.fps / scene.render.fps_base
        ch = settings.channel_filter

        def to_frame(time_sec):
            return settings.start_frame + round(time_sec * fps)

        def ch_match(e):
            return ch < 0 or e.channel == ch

        kicks = [e for e in events if e.note == settings.kick_note and ch_match(e)]
        crashes = [e for e in events if e.note == settings.crash_note and ch_match(e)]

        if not kicks and not crashes:
            self.report({"WARNING"}, "対象note(キック/クラッシュ)が見つかりませんでした。")
            return {"CANCELLED"}

        intensity_targets = [ld for ld, role in _iter_target_lights(settings)
                             if role in ("INTENSITY", "BOTH")]
        color_targets = [ld for ld, role in _iter_target_lights(settings)
                         if role in ("COLOR", "BOTH")]

        if settings.clear_existing:
            for ld in intensity_targets:
                _clear_keys(ld, "energy")
            for ld in color_targets:
                _clear_keys(ld, "color")

        key_count = 0

        # --- 強度(キック) ---
        base = settings.base_energy
        peak = settings.peak_energy
        attack = settings.attack_frames
        decay = settings.decay_frames

        for ld in intensity_targets:
            # 先頭にベースラインを1つ
            ld.energy = base
            ld.keyframe_insert("energy", frame=settings.start_frame)
            key_count += 1
            for e in kicks:
                f = to_frame(e.time_sec)
                this_peak = peak * (e.velocity / 127.0) if settings.use_velocity else peak
                # base -> peak -> base のパルス
                ld.energy = base
                ld.keyframe_insert("energy", frame=f)
                ld.energy = this_peak
                ld.keyframe_insert("energy", frame=f + attack)
                ld.energy = base
                ld.keyframe_insert("energy", frame=f + attack + decay)
                key_count += 3
            _set_interpolation(ld, "energy", "BEZIER")

        # --- 色(クラッシュ): パレット巡回、CONSTANT補間で保持 ---
        palette = [tuple(c.color) for c in settings.palette]
        if color_targets and crashes and palette:
            for ld in color_targets:
                # 先頭フレームに最初の色のベースライン
                ld.color = palette[0]
                ld.keyframe_insert("color", frame=settings.start_frame)
                key_count += 3  # color は3チャンネル
                for i, e in enumerate(crashes):
                    f = to_frame(e.time_sec)
                    ld.color = palette[i % len(palette)]
                    ld.keyframe_insert("color", frame=f)
                    key_count += 3
                _set_interpolation(ld, "color", "CONSTANT")

        self.report(
            {"INFO"},
            f"ベイク完了: キック{len(kicks)}件 / クラッシュ{len(crashes)}件, "
            f"キー{key_count}個挿入 (FPS={fps:g})",
        )
        return {"FINISHED"}


class MDL_OT_clear(Operator):
    bl_idname = "mdl.clear"
    bl_label = "クリア"
    bl_description = "対象ライトの強度・色キーフレームを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.mdl_settings
        n = 0
        for ld, role in _iter_target_lights(settings):
            if role in ("INTENSITY", "BOTH"):
                _clear_keys(ld, "energy")
            if role in ("COLOR", "BOTH"):
                _clear_keys(ld, "color")
            n += 1
        self.report({"INFO"}, f"{n}ライトのキーを削除しました。")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# パネル
# ---------------------------------------------------------------------------

class MDL_PT_panel(Panel):
    bl_label = "MIDI Drum Lights"
    bl_idname = "MDL_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MIDI Lights"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mdl_settings

        layout.prop(settings, "midi_path")

        box = layout.box()
        box.label(text="ライト(役割を指定)", icon="OUTLINER_OB_LIGHT")
        row = box.row()
        row.template_list("MDL_UL_lights", "", settings, "lights", settings, "lights_index", rows=3)
        col = row.column(align=True)
        col.operator("mdl.light_add", icon="ADD", text="")
        col.operator("mdl.light_remove", icon="REMOVE", text="")

        box = layout.box()
        box.label(text="色パレット(クラッシュで巡回)", icon="COLOR")
        row = box.row()
        row.template_list("MDL_UL_palette", "", settings, "palette", settings, "palette_index", rows=3)
        col = row.column(align=True)
        col.operator("mdl.palette_add", icon="ADD", text="")
        col.operator("mdl.palette_remove", icon="REMOVE", text="")

        box = layout.box()
        box.label(text="ノート割当")
        row = box.row(align=True)
        row.prop(settings, "kick_note")
        row.prop(settings, "crash_note")
        box.prop(settings, "channel_filter")

        box = layout.box()
        box.label(text="強度エンベロープ")
        box.prop(settings, "base_energy")
        box.prop(settings, "peak_energy")
        row = box.row(align=True)
        row.prop(settings, "attack_frames")
        row.prop(settings, "decay_frames")
        box.prop(settings, "use_velocity")

        layout.prop(settings, "start_frame")
        layout.prop(settings, "clear_existing")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("mdl.bake", icon="KEYFRAME_HLT")
        row.operator("mdl.clear", icon="TRASH")


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------

classes = (
    MDL_LightItem,
    MDL_PaletteColor,
    MDL_Settings,
    MDL_UL_lights,
    MDL_UL_palette,
    MDL_OT_light_add,
    MDL_OT_light_remove,
    MDL_OT_palette_add,
    MDL_OT_palette_remove,
    MDL_OT_bake,
    MDL_OT_clear,
    MDL_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mdl_settings = PointerProperty(type=MDL_Settings)


def unregister():
    del bpy.types.Scene.mdl_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
