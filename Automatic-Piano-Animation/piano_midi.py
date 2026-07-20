bl_info = {
    "name": "Piano MIDI Animator",
    "author": "",
    "version": (2, 0, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport > N Panel > Piano MIDI",
    "description": "MIDIファイルを読み込んでピアノ鍵盤のZ軸アニメーションを自動生成します",
    "category": "Animation",
}

import bpy
import os
import re
import struct
from bpy.props import (
    StringProperty, IntProperty, FloatProperty,
    BoolProperty, PointerProperty, CollectionProperty,
)
from bpy.types import PropertyGroup, Operator, Panel


# ===========================================================================
# Section 1: Pure-Python MIDI Parser（外部ライブラリ不使用）
# ===========================================================================

def read_vlq(data, pos):
    """
    SMF の可変長整数（Variable-Length Quantity）を読み取る。

    SMF では delta time などを節約するため、7ビット単位で値を格納し
    最上位ビット(bit7)が 1 の間は次のバイトへ続く、というエンコードを使う。
    例: 0x81 0x00 → 値 128

    戻り値: (読み取った値, 読み取り後のバイト位置)
    """
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)  # 下位7ビットを結合
        if not (byte & 0x80):                  # bit7が0なら終了
            break
    return value, pos


def parse_midi(filepath):
    """
    SMF (Standard MIDI File) を解析してノートのリストを返す。

    SMF の構造:
      MThd チャンク（ヘッダー）
        - フォーマット: 0=単一トラック, 1=複数トラック同期, 2=複数トラック非同期
        - トラック数
        - ticks_per_beat: 四分音符あたりのtick数（テンポ計算の基準）
      MTrk チャンク × N（各トラック）
        - デルタタイム + MIDIイベント の繰り返し
        - デルタタイムは「前のイベントからの相対tick数」

    戻り値:
      ticks_per_beat : int  四分音符あたりのtick数（通常 480）
      tempo_map      : [(abs_tick, us_per_beat), ...]
                       曲中のテンポ変化の一覧（tick→マイクロ秒/拍）
      note_pairs     : [(midi_note, on_tick, off_tick), ...]
                       全トラックから集めた発音区間（on_tick昇順）
    """
    with open(filepath, "rb") as f:
        data = f.read()

    # --- MThd ヘッダー検証 ---
    if data[:4] != b'MThd':
        raise ValueError("MIDIファイルではありません（MThd が見つかりません）")

    hdr_len = struct.unpack_from(">I", data, 4)[0]   # ヘッダーデータ長（通常6）
    fmt, num_tracks, tpb = struct.unpack_from(">HHH", data, 8)

    # SMPTE タイムコード形式（tpbの最上位ビットが1）は非対応
    if tpb & 0x8000:
        raise ValueError("SMPTE タイムコード形式の MIDI は未対応です")

    # デフォルトテンポ: 120 BPM = 500000 µs/拍
    # 曲中の Set Tempo イベント（Meta 0x51）でこれが上書きされる
    tempo_events = {0: 500000}

    all_note_events = []  # (abs_tick, midi_note, velocity) の一覧

    pos = 8 + hdr_len  # 最初の MTrk チャンクの先頭位置

    # --- 全トラックを順番に解析 ---
    for _ in range(num_tracks):
        if pos + 8 > len(data):
            break

        chunk_id  = data[pos:pos + 4]
        chunk_len = struct.unpack_from(">I", data, pos + 4)[0]
        chunk_start = pos + 8
        chunk_end   = chunk_start + chunk_len
        pos = chunk_end  # 次のチャンク先頭へ移動（MTrk 以外もスキップ）

        if chunk_id != b'MTrk':
            continue  # MTrk 以外のチャンクは無視

        # --- トラック内のイベントを逐次読み取り ---
        tp             = chunk_start
        abs_tick       = 0       # 絶対tick（デルタの累積）
        running_status = None    # ランニングステータス（同じイベントタイプが続く場合の省略）

        while tp < chunk_end:
            # デルタタイムを読んで絶対tickに加算
            delta, tp = read_vlq(data, tp)
            abs_tick += delta

            if tp >= chunk_end:
                break

            byte = data[tp]

            # --- Meta Event (0xFF XX len data...) ---
            # テンポ変更やトラック名など。MIDIチャンネルとは無関係。
            if byte == 0xFF:
                tp += 1
                if tp >= chunk_end:
                    break
                meta_type = data[tp]
                tp += 1
                meta_len, tp = read_vlq(data, tp)

                if meta_type == 0x51 and meta_len == 3:
                    # Set Tempo: 3バイトでマイクロ秒/拍を表す
                    us = (data[tp] << 16) | (data[tp + 1] << 8) | data[tp + 2]
                    tempo_events[abs_tick] = us

                tp += meta_len
                running_status = None  # Meta Event はランニングステータスをリセット
                continue

            # --- SysEx (0xF0 or 0xF7) ---
            # システムエクスクルーシブ。ノート情報は含まない。
            if byte == 0xF0 or byte == 0xF7:
                tp += 1
                sysex_len, tp = read_vlq(data, tp)
                tp += sysex_len
                running_status = None  # SysEx もランニングステータスをリセット
                continue

            # --- 通常の MIDI チャンネルイベント ---
            # bit7 が 1 → 新しいステータスバイト（イベント種別＋チャンネル）
            # bit7 が 0 → ランニングステータスの続き（前回と同じイベント種別）
            if byte & 0x80:
                running_status = byte
                tp += 1

            if running_status is None:
                # ステータスバイトが一度も来ていない状態でデータバイトが来た場合は読み飛ばす
                tp += 1
                continue

            # 上位4ビット = イベント種別, 下位4ビット = MIDIチャンネル（今回は無視）
            event_type = (running_status >> 4) & 0x0F

            if event_type == 0x9:
                # Note On: note番号(1byte) + velocity(1byte)
                # velocity=0 は「Note Off と同義」という慣習に注意
                if tp + 1 >= chunk_end:
                    break
                note = data[tp]
                vel  = data[tp + 1]
                tp += 2
                all_note_events.append((abs_tick, note, vel))

            elif event_type == 0x8:
                # Note Off: note番号(1byte) + velocity(1byte、通常0で無視)
                if tp + 1 >= chunk_end:
                    break
                note = data[tp]
                tp += 2
                all_note_events.append((abs_tick, note, 0))  # velocity=0 で統一

            elif event_type in (0xA, 0xB, 0xE):
                # Aftertouch / Control Change / Pitch Bend: データ2バイト
                tp += 2

            elif event_type in (0xC, 0xD):
                # Program Change / Channel Pressure: データ1バイト
                tp += 1

            else:
                # 未知のイベント: 1バイト読み飛ばして継続
                tp += 1

    # --- テンポマップを絶対tick昇順に整列 ---
    # 同一tickに複数のテンポ変更がある場合、辞書により後のものが残る
    tempo_map = sorted(tempo_events.items())

    # --- 全トラックのノートイベントを時系列順にソート ---
    all_note_events.sort(key=lambda e: e[0])

    # --- Note On と Note Off をペアリングして「発音区間」を作る ---
    # ピッチごとに FIFO キューを使い、最初の Note On に最初の Note Off を対応させる
    pending    = {}   # midi_note → [on_tick, ...] の待ちリスト
    note_pairs = []   # 完成した (midi_note, on_tick, off_tick) のリスト

    for abs_tick, note, vel in all_note_events:
        if vel > 0:
            # Note On: キューに積む
            pending.setdefault(note, []).append(abs_tick)
        else:
            # Note Off (または velocity=0 の Note On): キューの先頭とペアリング
            if pending.get(note):
                on_tick = pending[note].pop(0)
                note_pairs.append((note, on_tick, abs_tick))

    # ペアリングできなかった Note On（曲末などで Off が来ていない）にフォールバック
    for note, ticks in pending.items():
        for on_tick in ticks:
            note_pairs.append((note, on_tick, on_tick + tpb))  # 1拍分の長さとみなす

    # 発音開始tick順にソートして返す
    note_pairs.sort(key=lambda p: p[1])

    return tpb, tempo_map, note_pairs


# ===========================================================================
# Section 2: 時間変換
# ===========================================================================

def tick_to_frame(tick, tempo_map, ticks_per_beat, fps, frame_offset=1):
    """
    MIDI の絶対tick値を Blender のフレーム番号に変換する。

    MIDI では時間を「tick」という単位で表す。
    実際の時間（秒）への変換には「テンポ（1拍あたりのマイクロ秒）」が必要で、
    曲中でテンポが変わるため、テンポマップを参照しながら区間ごとに積算する。

    手順:
      1. テンポマップを順番にたどり、各テンポ区間の秒数を計算して合計する
      2. 合計秒数 × fps で Blender フレーム番号に変換
      3. frame_offset を足して開始フレームを調整（デフォルト1でフレーム1始まり）
    """
    time_s     = 0.0
    prev_tick  = 0
    prev_tempo = 500000  # デフォルトテンポ（120 BPM）

    for map_tick, map_tempo in tempo_map:
        if map_tick >= tick:
            break  # 変換したい tick を超えたら終了
        # この区間（prev_tick ～ map_tick）の秒数を加算
        time_s    += (map_tick - prev_tick) / ticks_per_beat * (prev_tempo / 1_000_000.0)
        prev_tick  = map_tick
        prev_tempo = map_tempo

    # 最後の区間（最後のテンポ変更 ～ tick）の秒数を加算
    time_s += (tick - prev_tick) / ticks_per_beat * (prev_tempo / 1_000_000.0)

    return time_s * fps + frame_offset


# ===========================================================================
# Section 3: 鍵盤マッピング
# ===========================================================================

# 半音（0〜11）に対する MIDI の音名インデックス
# C=0, C#=1, D=2, D#=3, E=4, F=5, F#=6, G=7, G#=8, A=9, A#=10, B=11
# この順序は「最低オクターブ割り当てリスト」のインデックスと対応させる
SEMITONE_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
# ユーザーが UI で割り当てるときに表示する音名ラベル（表示用）
NOTE_LABELS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def build_note_to_object_map(key_assignments, lowest_midi_note, num_octaves=5):
    """
    ユーザーが設定した「最低オクターブの各音のオブジェクト名」をもとに、
    全オクターブ分の「MIDIノート番号 → オブジェクト名」辞書を構築する。

    ロジック:
      - key_assignments: 最低オクターブの C〜B（半音0〜11）のオブジェクト名リスト
        インデックス i が半音 i に対応。未割り当ては空文字列。
      - 各音のオブジェクト名の末尾にある数字を読み取り、
        オクターブが1つ上がるたびにその数字を +1 した名前を生成する。
      - 例: "1.C.001" の最低オクターブ割り当てなら
             1オクターブ上は "1.C.002", さらに上は "1.C.003" ...

    戻り値: {midi_note_number: object_name, ...}
    """
    note_map = {}

    for semitone in range(12):
        base_name = key_assignments[semitone]
        if not base_name:
            continue  # このピッチは割り当てなし（鍵盤に存在しない音など）

        # オブジェクト名末尾の数字部分を正規表現で取得
        # 例: "1.C.001" → prefix="1.C.", num_str="001", num=1
        match = re.search(r'^(.*?)(\d+)$', base_name)
        if not match:
            continue  # 数字で終わらない名前は処理できない

        name_prefix = match.group(1)  # 数字より前の部分（例: "1.C."）
        base_num    = int(match.group(2))  # 最低オクターブの番号（例: 1）
        num_digits  = len(match.group(2))  # ゼロ埋め桁数（例: 3 → "001"）

        # 全オクターブ分のマッピングを生成
        for oct_offset in range(num_octaves + 1):  # 61鍵は5オクターブ+最高音のC
            midi_note   = lowest_midi_note + semitone + (oct_offset * 12)

            # 61鍵の最高音 C（= lowest_midi_note + 60）を超えたら生成しない
            if midi_note > lowest_midi_note + 60:
                break

            suffix      = base_num + oct_offset  # オクターブ分だけ番号を増やす
            obj_name    = f"{name_prefix}{suffix:0{num_digits}d}"
            note_map[midi_note] = obj_name

    return note_map


# ===========================================================================
# Section 4: プロパティ定義
# ===========================================================================

class KeyAssignmentItem(PropertyGroup):
    """
    最低オクターブの1つの音（C〜B の半音12種類のうちの1つ）に
    割り当てるオブジェクトを保持するアイテム。

    CollectionProperty として12個作成し、
    インデックス 0=C, 1=C#, 2=D, ... 11=B に対応させる。
    """
    # 割り当てられたオブジェクトへの参照
    obj: PointerProperty(
        name="オブジェクト",
        type=bpy.types.Object,
    )


class PianoProperties(PropertyGroup):
    """
    アドオン全体の設定をシーンに保存するプロパティグループ。
    bpy.types.Scene.piano_props として登録される。
    """

    # --- MIDI ファイルパス ---
    midi_file: StringProperty(
        name="MIDIファイル",
        description="アニメーションに使用する MIDI ファイルのパス",
        subtype="FILE_PATH",
        default="",
    )

    # --- ピアノコレクション ---
    piano_collection: PointerProperty(
        name="ピアノコレクション",
        description="61鍵の鍵盤オブジェクトが入っているコレクション",
        type=bpy.types.Collection,
    )

    # --- 最低オクターブの12音の割り当てリスト ---
    # UI では「C, C#, D, D#, E, F, F#, G, G#, A, A#, B」の順に表示する
    key_assignments: CollectionProperty(type=KeyAssignmentItem)

    # --- 61鍵の最低音の MIDI ノート番号 ---
    # 標準的な61鍵盤は C2(=36) から C7(=96)
    lowest_midi_note: IntProperty(
        name="最低音 MIDI 番号",
        description="61鍵ピアノの最低音の MIDI ノート番号（C2=36, C3=48）",
        default=36,
        min=0,
        max=127,
    )

    # --- 押鍵アニメーションの設定 ---
    press_depth: FloatProperty(
        name="押下量 (Z)",
        description="鍵盤を押したときの Z 軸方向の移動量（マイナスで下方向）",
        default=-0.01,
        min=-1.0,
        max=0.0,
        precision=4,
    )
    press_frames: IntProperty(
        name="押下フレーム数",
        description="鍵盤が最大まで押し下がるまでのフレーム数",
        default=2,
        min=1,
        max=20,
    )
    release_frames: IntProperty(
        name="リリースフレーム数",
        description="鍵盤が元の位置に戻るまでのフレーム数",
        default=2,
        min=1,
        max=20,
    )

    # --- フレームオフセット ---
    # 曲の先頭をフレーム1（またはユーザー指定値）に合わせるための加算値
    frame_offset: IntProperty(
        name="フレームオフセット",
        description="全キーフレームに加算するオフセット（1 = フレーム1から開始）",
        default=1,
        min=0,
    )

    # --- 既存アニメーションのクリア ---
    clear_existing: BoolProperty(
        name="既存アニメーションをクリア",
        description="アニメーション生成前に既存のキーフレームをすべて削除する",
        default=True,
    )

    # --- 実行結果表示用（UI に「前回の結果」を表示するために使う）---
    # -1 = まだ一度も実行していない（初期値）
    status_notes:    IntProperty(default=-1, options={"HIDDEN"})
    status_animated: IntProperty(default=0,  options={"HIDDEN"})
    status_missing:  IntProperty(default=0,  options={"HIDDEN"})


# ===========================================================================
# Section 5: オペレーター定義
# ===========================================================================

class PIANO_OT_BrowseMIDI(Operator):
    """
    ファイルブラウザを開いて MIDI ファイルを選択するオペレーター。
    N パネルの「参照」ボタンに割り当てる。
    """
    bl_idname = "piano.browse_midi"
    bl_label  = "MIDIファイルを選択"

    filepath:    StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.mid;*.midi", options={"HIDDEN"})

    def execute(self, context):
        context.scene.piano_props.midi_file = self.filepath
        return {"FINISHED"}

    def invoke(self, context, event):
        # ファイルブラウザを開いてモーダル状態に移行
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class PIANO_OT_InitKeyAssignments(Operator):
    """
    鍵盤割り当てリスト（key_assignments）を初期化するオペレーター。
    アドオン初回起動時や「リセット」ボタンで呼び出す。
    CollectionProperty は自動では12個にならないため、手動で追加する必要がある。
    """
    bl_idname = "piano.init_key_assignments"
    bl_label  = "割り当てを初期化"

    def execute(self, context):
        props = context.scene.piano_props
        # 既存のアイテムをすべて削除してから12個追加
        props.key_assignments.clear()
        for _ in range(12):
            props.key_assignments.add()
        return {"FINISHED"}


class PIANO_OT_GenerateAnimation(Operator):
    """
    メインのオペレーター。
    MIDI ファイルを解析し、鍵盤割り当てをもとに各オブジェクトへ
    Z 軸のキーフレームを自動挿入する。
    """
    bl_idname = "piano.generate_animation"
    bl_label  = "アニメーションを生成"
    bl_options = {"REGISTER", "UNDO"}  # Ctrl+Z でアンドゥ可能にする

    @classmethod
    def poll(cls, context):
        """実行可能かどうかの判定。MIDI ファイルと割り当てが設定済みならTrue。"""
        props = context.scene.piano_props
        has_midi        = bool(props.midi_file)
        has_assignments = (
            len(props.key_assignments) == 12 and
            any(item.obj is not None for item in props.key_assignments)
        )
        return has_midi and has_assignments

    def execute(self, context):
        props = context.scene.piano_props

        # --- 1. 入力検証 ---
        midi_path = bpy.path.abspath(props.midi_file)
        if not os.path.isfile(midi_path):
            self.report({"ERROR"}, f"MIDIファイルが見つかりません: {midi_path}")
            return {"CANCELLED"}

        if len(props.key_assignments) != 12:
            self.report({"ERROR"}, "鍵盤割り当てが初期化されていません。「割り当てを初期化」を押してください")
            return {"CANCELLED"}

        # --- 2. MIDI ファイルを解析 ---
        try:
            tpb, tempo_map, note_pairs = parse_midi(midi_path)
        except Exception as e:
            self.report({"ERROR"}, f"MIDI 解析エラー: {e}")
            return {"CANCELLED"}

        # --- 3. 最低オクターブの割り当てからオブジェクト名を取得 ---
        # key_assignments[i].obj が None の場合は空文字列として扱う
        key_assignment_names = [
            (item.obj.name if item.obj is not None else "")
            for item in props.key_assignments
        ]

        # --- 4. 全オクターブ分の「MIDIノート → オブジェクト名」辞書を構築 ---
        # build_note_to_object_map が末尾の数字を読んで +1 しながら展開する
        note_to_obj = build_note_to_object_map(
            key_assignment_names,
            props.lowest_midi_note,
            num_octaves=5,
        )

        # --- 5. コレクション内の全オブジェクトを名前で引けるように辞書化 ---
        # （コレクションが未選択でも割り当て済みオブジェクトは直接参照できるが、
        #   clear_existing のためにコレクション全体も使う）
        collection = props.piano_collection
        if collection:
            col_obj_dict = {obj.name: obj for obj in collection.all_objects}
        else:
            col_obj_dict = {}

        # --- 6. 既存アニメーションをクリア ---
        if props.clear_existing and collection:
            for obj in collection.all_objects:
                if obj.animation_data:
                    obj.animation_data_clear()

        # --- 7. アニメーションパラメータを取得 ---
        # シーンの FPS 設定を取得（fps_base で割ることで正確な値を得る）
        fps          = context.scene.render.fps / context.scene.render.fps_base
        frame_offset = props.frame_offset
        depth        = props.press_depth
        press_f      = props.press_frames
        release_f    = props.release_frames

        # --- 8. ノートごとのキーフレームデータを収集 ---
        # 同一オブジェクトに複数のノートが割り当たる場合は辞書でまとめる
        # frame_data: {obj_name: {frame_number: z_value, ...}}
        frame_data    = {}
        missing_notes = set()   # マッピングに存在しないMIDIノート番号
        notes_processed = 0

        for midi_note, on_tick, off_tick in note_pairs:
            # このMIDIノートに対応するオブジェクト名を取得
            obj_name = note_to_obj.get(midi_note)
            if obj_name is None:
                missing_notes.add(midi_note)
                continue  # 割り当て外のノートはスキップ

            # オブジェクト名に対応するオブジェクトを確認
            # （割り当てアイテムから直接引く）
            target_obj = None
            for item in props.key_assignments:
                if item.obj and item.obj.name == obj_name:
                    target_obj = item.obj
                    break
            if target_obj is None and obj_name in col_obj_dict:
                target_obj = col_obj_dict[obj_name]
            if target_obj is None:
                missing_notes.add(midi_note)
                continue

            # tick → フレーム番号に変換
            frame_on  = tick_to_frame(on_tick,  tempo_map, tpb, fps, frame_offset)
            frame_off = tick_to_frame(off_tick, tempo_map, tpb, fps, frame_offset)
            note_dur  = frame_off - frame_on

            # 短いノートでも押鍵・リリースが重ならないよう、
            # デュレーションの 40% を上限としてクランプする
            p = min(float(press_f),   max(0.5, note_dur * 0.4))
            r = min(float(release_f), max(0.5, note_dur * 0.4))

            # このオブジェクトのフレームデータ辞書を初期化（初回のみ）
            if obj_name not in frame_data:
                frame_data[obj_name] = {"obj": target_obj, "frames": {}}

            fd = frame_data[obj_name]["frames"]

            # 5点キーフレームで押鍵アニメーションを定義:
            #   [guard]   押下の1フレーム前: Z=0（前のノートのリリース後を保証）
            #   [on]      押下開始フレーム: Z=0（まだ押していない）
            #   [on+p]    最大押下フレーム: Z=depth（鍵盤が一番下）
            #   [off]     ノートオフフレーム: Z=depth（押しっぱなしで保持）
            #   [off+r]   リリース完了フレーム: Z=0（鍵盤が元の位置に戻る）
            guard = max(1.0, frame_on - 1.0)
            fd[guard]          = 0.0
            fd[frame_on]       = 0.0
            fd[frame_on + p]   = depth
            fd[frame_off]      = depth
            fd[frame_off + r]  = 0.0

            notes_processed += 1

        # --- 9. FCurve にキーフレームを一括挿入 ---
        keys_animated = 0
        for obj_name, entry in frame_data.items():
            obj = entry["obj"]
            fd  = entry["frames"]

            # アニメーションデータが未作成なら作成する
            obj.animation_data_create()

            # アクション（FCurve のコンテナ）を取得または新規作成
            if obj.animation_data.action is None:
                action = bpy.data.actions.new(name=f"PianoMIDI_{obj_name}")
                obj.animation_data.action = action
            else:
                action = obj.animation_data.action

            # Z Location（location の index=2）の FCurve を取得または作成
            # location は [X=0, Y=1, Z=2] の順
            fcu = action.fcurves.find("location", index=2)
            if fcu is None:
                fcu = action.fcurves.new(data_path="location", index=2)

            # キーフレームをフレーム番号順にソートして一括登録
            sorted_kf = sorted(fd.items())           # [(frame, z), ...]
            fcu.keyframe_points.add(len(sorted_kf))  # 必要な数だけ点を確保

            for i, (frame, z) in enumerate(sorted_kf):
                kp             = fcu.keyframe_points[i]
                kp.co          = (float(frame), float(z))  # (フレーム番号, Z値)
                kp.interpolation = "LINEAR"                 # 直線補間

            fcu.update()  # FCurve のキャッシュを更新（必須）
            keys_animated += 1

        # --- 10. 実行結果を保存して報告 ---
        props.status_notes    = notes_processed
        props.status_animated = keys_animated
        props.status_missing  = len(missing_notes)

        msg = f"完了: {notes_processed} ノート処理、{keys_animated} 鍵盤アニメート"
        if missing_notes:
            msg += f"、{len(missing_notes)} MIDIノートが未割り当て"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)

        return {"FINISHED"}


# ===========================================================================
# Section 6: UI パネル定義
# ===========================================================================

class _PianoMixin:
    """全パネル共通の設定をまとめた Mixin クラス。"""
    bl_category  = "Piano MIDI"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"


class PIANO_PT_Main(_PianoMixin, Panel):
    """
    メインパネル。
    MIDIファイルの選択・鍵盤割り当て・アニメーション生成ボタンを配置する。
    """
    bl_label   = "Piano MIDI Animator"
    bl_idname  = "PIANO_PT_Main"

    def draw(self, context):
        layout = self.layout
        props  = context.scene.piano_props

        # --- MIDIファイル選択 ---
        box = layout.box()
        box.label(text="MIDIファイル", icon="FILE_SOUND")
        row = box.row(align=True)
        row.prop(props, "midi_file", text="")
        row.operator("piano.browse_midi", text="", icon="FILE_FOLDER")

        # --- ピアノコレクション選択（clear_existing 用） ---
        layout.separator(factor=0.5)
        layout.prop(props, "piano_collection", icon="OUTLINER_COLLECTION")
        layout.label(text="※コレクションは「既存クリア」機能にのみ使用", icon="INFO")

        # --- 最低音 MIDI 番号 ---
        layout.separator(factor=0.5)
        layout.prop(props, "lowest_midi_note")

        # --- 最低オクターブの鍵盤割り当て ---
        layout.separator()
        box = layout.box()
        box.label(text="最低オクターブの鍵盤割り当て (C〜B)", icon="OBJECT_DATA")

        # key_assignments が未初期化（12個揃っていない）なら初期化ボタンを表示
        if len(props.key_assignments) != 12:
            box.operator("piano.init_key_assignments", icon="FILE_REFRESH")
        else:
            # 12音分のオブジェクト選択フィールドを音名ラベルと並べて表示
            col = box.column(align=True)
            for i, label in enumerate(NOTE_LABELS):
                row = col.row(align=True)
                # 音名ラベル（固定幅に見せるためスペースで補完）
                row.label(text=f"{label:<3}")
                row.prop(props.key_assignments[i], "obj", text="")

        # --- アニメーション生成ボタン ---
        layout.separator()
        row = layout.row()
        row.scale_y = 2.0
        row.operator("piano.generate_animation", icon="PLAY")

        # --- 実行結果の表示（一度でも実行されていれば表示） ---
        if props.status_notes >= 0:
            layout.separator(factor=0.5)
            result_box = layout.box()
            result_box.label(text="実行結果", icon="CHECKMARK")
            col = result_box.column(align=True)
            col.label(text=f"処理ノート数:     {props.status_notes}")
            col.label(text=f"アニメートキー数: {props.status_animated}")
            if props.status_missing > 0:
                row = col.row()
                row.alert = True
                row.label(
                    text=f"未割り当てノート: {props.status_missing} 個",
                    icon="ERROR",
                )


class PIANO_PT_Settings(_PianoMixin, Panel):
    """
    詳細設定パネル（折りたたみ）。
    押鍵アニメーションの細かい数値やフレームオフセットを設定する。
    """
    bl_label     = "アニメーション設定"
    bl_idname    = "PIANO_PT_Settings"
    bl_parent_id = "PIANO_PT_Main"
    bl_options   = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props  = context.scene.piano_props
        layout.use_property_split    = True
        layout.use_property_decorate = False

        # 押鍵アニメーションのパラメータ
        col = layout.column(align=True)
        col.prop(props, "press_depth")
        col.prop(props, "press_frames")
        col.prop(props, "release_frames")

        layout.separator(factor=0.5)

        layout.prop(props, "frame_offset")
        layout.prop(props, "clear_existing")


# ===========================================================================
# Section 7: アドオン登録・解除
# ===========================================================================

# 登録するクラスの一覧（登録順序に依存関係がある場合は上から順に）
CLASSES = [
    KeyAssignmentItem,         # CollectionProperty のアイテム型（先に登録が必要）
    PianoProperties,           # シーンに保存するプロパティ群
    PIANO_OT_BrowseMIDI,       # ファイル参照オペレーター
    PIANO_OT_InitKeyAssignments,  # 割り当て初期化オペレーター
    PIANO_OT_GenerateAnimation,   # メインのアニメーション生成オペレーター
    PIANO_PT_Main,             # N パネル：メイン
    PIANO_PT_Settings,         # N パネル：詳細設定（サブパネル）
]


def register():
    """アドオンを有効化したときに呼ばれる。クラスを登録してシーンプロパティを追加。"""
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Scene に piano_props プロパティを追加（全シーンからアクセス可能になる）
    bpy.types.Scene.piano_props = PointerProperty(type=PianoProperties)


def unregister():
    """アドオンを無効化したときに呼ばれる。逆順でクラスを解除してプロパティを削除。"""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.piano_props


if __name__ == "__main__":
    # Blender の Text Editor から直接実行した場合に登録する
    register()
