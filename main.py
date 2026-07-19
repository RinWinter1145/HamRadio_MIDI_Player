#!/usr/bin/env python3
#！？滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木滚木？！
import socket
import time
import struct
import sys
import argparse
from typing import Dict, List, Tuple, Optional

#midi解析

def read_vlq(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value, pos


NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def note_name(midi_note: int) -> str:
    #midi音符转音调
    octave = midi_note // 12 - 1
    return f"{NOTE_NAMES[midi_note % 12]}{octave}"


def parse_midi(filepath: str) -> Tuple[List[Tuple[int, float, float]], float]:
    #midi文件解析
    with open(filepath, 'rb') as f:
        data = f.read()

    pos = 0
    # MThd
    assert data[pos:pos+4] == b'MThd', "不是有效的 MIDI 文件"
    pos += 4
    pos += 4
    pos += 2
    num_tracks = struct.unpack('>H', data[pos:pos+2])[0]
    pos += 2
    ticks_per_beat = struct.unpack('>H', data[pos:pos+2])[0]
    pos += 2

    all_notes: List[Tuple[int, int, int]] = []  # (note, start_tick, duration_tick)
    tempo = 500000

    for _ in range(num_tracks):
        assert data[pos:pos+4] == b'MTrk'
        pos += 4
        track_len = struct.unpack('>I', data[pos:pos+4])[0]
        pos += 4
        track_end = pos + track_len
        abs_time = 0
        pending: Dict[int, int] = {}
        running_status = 0

        while pos < track_end:
            delta, pos = read_vlq(data, pos)
            abs_time += delta
            byte = data[pos]
            if byte & 0x80:
                status = byte
                pos += 1
                running_status = status
            else:
                status = running_status

            if status == 0xFF:
                meta_type = data[pos]
                pos += 1
                length, pos = read_vlq(data, pos)
                if meta_type == 0x51:
                    tempo = struct.unpack('>I', b'\x00' + data[pos:pos+length])[0]
                pos += length
            elif (status & 0xF0) == 0x90: 
                note = data[pos]; pos += 1
                vel = data[pos]; pos += 1
                if vel > 0:
                    pending[note] = abs_time
                elif note in pending:
                    start = pending.pop(note)
                    all_notes.append((note, start, abs_time - start))
            elif (status & 0xF0) == 0x80:
                note = data[pos]; pos += 1
                pos += 1
                if note in pending:
                    start = pending.pop(note)
                    all_notes.append((note, start, abs_time - start))
            elif (status & 0xF0) in (0xB0, 0xE0):
                pos += 2
            elif (status & 0xF0) == 0xC0:
                pos += 1
            elif (status & 0xF0) == 0xD0:
                pos += 1
            else:
                pos += 1

        for note, start in pending.items():
            all_notes.append((note, start, abs_time - start))

    all_notes.sort(key=lambda x: (x[1], x[0]))

    #tick转秒
    bpm = 60_000_000 / tempo
    us_per_tick = tempo / ticks_per_beat

    notes_sec = [(n, st * us_per_tick / 1_000_000, dur * us_per_tick / 1_000_000)
                 for n, st, dur in all_notes]

    return notes_sec, bpm

#频率计算

def freq_from_midi(midi_note: int) -> float:
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

def build_freq_table(notes: List[Tuple[int, float, float]], f_int: int) -> Dict[str, int]:
    #F_dial = F_int + F_audio
    table = {"gunmu": f_int}
    seen = set()
    for n, _, _ in notes:
        name = note_name(n)
        if name not in seen:
            seen.add(name)
            audio = freq_from_midi(n)
            table[name] = round(f_int + audio)
    return table

#hamlib

class RigController:
    def __init__(self, host: str = '127.0.0.1', port: int = 4532,
                 timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect((self.host, self.port))
            print(f"已连接到设备 ({self.host}:{self.port})")
            return True
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"无法连接设备: {e}")
            return False

    def _send_command(self, cmd: str) -> str:
        if not self.sock:
            raise ConnectionError("未连接到设备")
        self.sock.sendall(cmd.encode() + b'\n')
        response = self.sock.recv(1024).decode().strip()
        if response.startswith('RPRT '):
            err_code = int(response.split()[1])
            if err_code != 0:
                raise RuntimeError(f"Hamlib错误 {err_code}: 命令 '{cmd}' 失败")
        return response

    def set_frequency(self, freq_hz: int):
        self._send_command(f"F {freq_hz}")

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

#播放

class RadioMIDIPlayer:
    def __init__(self, midi_path: str, f_int: int,
                 rig_host: str = '127.0.0.1', rig_port: int = 4532):
        self.f_int = f_int
        self.rig = RigController(rig_host, rig_port)

        print(f"干扰源频率: {f_int} Hz")

        print("解析 MIDI ...")
        self.notes, self.bpm = parse_midi(midi_path)
        print(f"共 {len(self.notes)} 个音符, BPM={self.bpm:.0f}")

        print("正在计算频率表 ...")
        self.tone_table = build_freq_table(self.notes, f_int)
        print(f"已生成 {len(self.tone_table)} 个频率")

        if self.notes:
            last_end = max((s + d) for _, s, d in self.notes)
            print(f"总时长: {last_end:.1f} 秒 ({last_end/60:.1f} 分钟)")

    def play(self):
        if not self.rig.connect():
            print("无法连接电台，退出")
            return

        print(f"\n开始播放 ({len(self.notes)} 个音符)")
        print("Ctrl+C 停止")
        print("-" * 50)

        gunmu_freq = self.tone_table.get("gunmu", None)

        t0 = time.time()
        try:
            for i, (note, start, dur) in enumerate(self.notes):
                name = note_name(note)

                # 等到音符开始时间
                wait = start - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)

                freq = self.tone_table.get(name)
                if freq is None:
                    print(f"跳过 {name} (无数据)")
                    continue

                self.rig.set_frequency(freq)
                print(f"{name:>4s}  {freq}Hz  {dur*1000:.0f}ms")

                remain = start + dur - (time.time() - t0)
                if remain > 0:
                    time.sleep(remain)

                if gunmu_freq:
                    self.rig.set_frequency(gunmu_freq)

                if (i + 1) % 200 == 0:
                    print(f"进度: {i+1}/{len(self.notes)}  "
                          f"已播{time.time()-t0:.0f}s")

        except KeyboardInterrupt:
            print("\n播放已停止")

        finally:
            if gunmu_freq:
                self.rig.set_frequency(gunmu_freq)
            self.rig.close()
            print(f"播放结束 ({time.time()-t0:.1f}s)")

def main():
    parser = argparse.ArgumentParser(
        description='业余无线电台MIDI播放器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""?"""
    )
    parser.add_argument('--midi', default='music.mid',
                        help='MIDI 文件路径 (默认: music.mid)')
    parser.add_argument('--host', default='127.0.0.1',
                        help='rigctld 主机地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=4532,
                        help='rigctld 端口 (默认: 4532)')
    parser.add_argument('--freq', type=int, default=None,
                        help='干扰源频率(Hz)，若不提供则交互式输入')

    args = parser.parse_args()
    f_int = args.freq
    if f_int is None:
        try:
            f_int = int(input("请输入干扰源频率 (Hz): ").strip())
        except (ValueError, EOFError):
            print("输入无效")
            return

    RadioMIDIPlayer(args.midi, f_int, args.host, args.port).play()


if __name__ == '__main__':
    main()
