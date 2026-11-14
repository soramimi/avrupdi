"""Boundary tests use the real Updi and SerialPort classes over a PTY."""
import os
import pathlib
import pty
import subprocess
import tempfile
import time
import unittest
import test_serialupdi as serial
from test_nvm import block_read


class BlockReadTest(unittest.TestCase):
    run_device = serial.SerialUpdiTest.run_device
    read_exact = serial.SerialUpdiTest.read_exact

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.app = pathlib.Path(cls.tmp.name) / 'block_read'
        source = pathlib.Path(__file__).with_name('block_read.cpp')
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Wpedantic',
                        '-pthread', str(source), '-o', str(cls.app)], check=True)

    def check_read(self, address, size, fragment=False):
        data = bytes((i * 37 + 19) & 255 for i in range(size))
        self.run_device(block_read(address, data), 0, data.hex().upper().encode() + b' OK',
                        args=[str(address), str(size)], app=self.app, fragment=fragment)

    def test_repeat_boundaries_and_chunk_restart(self):
        for size in [0, 1, 2, 63, 64, 255, 256, 257, 512, 513]:
            with self.subTest(size=size): self.check_read(0x8000, size)

    def test_byte_address_boundary_crossing_and_last_address(self):
        for address, size in [(0x80ff, 2), (0x87c0, 64), (0x143f, 1),
                              (0xff00, 256), (0xffff, 1), (0xffff, 0)]:
            with self.subTest(address=address, size=size): self.check_read(address, size)

    def test_address_wrap_rejected_before_transmission(self):
        for address, size in [(0xffff, 2), (0xff00, 257), (0, 65537)]:
            with self.subTest(address=address, size=size):
                self.run_device([], 1, b'crosses 16-bit address boundary',
                                args=[str(address), str(size)], app=self.app)

    def test_fragmented_maximum_block(self):
        self.check_read(0x8000, 256, fragment=True)

    def test_later_chunk_timeout_does_not_return_partial_result(self):
        data = bytes(range(256)) + b'X'
        script = block_read(0x8000, data)
        script[-1] = serial.packet('55 24')
        self.run_device(script, 1, b'timeout waiting for serial response',
                        args=['32768', '257'], app=self.app)

    def test_24_bit_boundaries_and_pointer_restarts(self):
        from test_avr64du32 import block
        for address, size in [(0xffff, 257), (0x800000, 512), (0x80ff00, 256),
                              (0xffffff, 1), (0xffffff, 0)]:
            with self.subTest(address=address, size=size):
                data = bytes((i * 17 + 3) & 255 for i in range(size))
                self.run_device(block(address, data), 0, data.hex().upper().encode() + b' OK',
                                args=[str(address), str(size), 'AVR64DU32'], app=self.app)

    def test_24_bit_overflow_rejected_before_transmission(self):
        for address, size in [(0xffffff, 2), (0xffff00, 257), (0, 0x1000001)]:
            with self.subTest(address=address, size=size):
                self.run_device([], 1, b'crosses 24-bit address boundary',
                                args=[str(address), str(size), 'AVR64DU32'], app=self.app)

    def test_partial_response_does_not_extend_deadline(self):
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(self.app), os.ttyname(slave), '32768', '64'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for expected, response in block_read(0x8000, bytes(64))[:-1]:
                serial.SerialUpdiTest.exchange(self, master, expected, response)
            self.assertEqual(self.read_exact(master, 2), bytes.fromhex('55 24'))
            os.write(master, bytes.fromhex('55 24'))
            start = time.monotonic()
            # Keep making progress, but never provide the whole block within 1 s.
            for _ in range(4):
                os.write(master, b'\x00')
                time.sleep(0.2)
            out, err = proc.communicate(timeout=0.65)
            self.assertLess(time.monotonic() - start, 1.45)
            self.assertEqual(proc.returncode, 1, (out, err))
            self.assertEqual(out, b'')
            self.assertIn(b'timeout waiting for serial response', err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            os.close(slave)
