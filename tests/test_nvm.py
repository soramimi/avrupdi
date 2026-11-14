"""ATtiny202 NVM v0 transcripts: byte addresses, 16-bit Flash stores, two ACKs."""
import os
import pty
import time
import pathlib
import subprocess
import tempfile
import unittest
import test_serialupdi as serial
from test_serialupdi import APP, CLEANUP, packet


def read(address, value):
    return packet(f'55 04 {address & 255:02x} {address >> 8:02x}', bytes([value]))


def store(address, data, ack=b'\x40'):
    return [packet(f'55 {0x44 + (len(data) == 2):02x} {address & 255:02x} {address >> 8:02x}', ack),
            (bytes(data), b'\x40')]


READY = [read(0x1002, 0)]


def block_read(address, data):
    script = []
    for offset in range(0, len(data), 256):
        chunk = data[offset:offset + 256]
        a = address + offset
        script.append(packet(f'55 69 {a & 255:02x} {a >> 8:02x}', b'\x40'))
        if len(chunk) > 1:
            script.append(packet(f'55 a0 {len(chunk) - 1:02x}'))
        script.append(packet('55 24', chunk))
    return script


def page_read(page, data):
    return READY + block_read(0x8000 + page * 64, data)


def program(page, data):
    script = READY + store(0x1000, [4]) + READY
    for i in range(0, 64, 2):
        script += store(0x8000 + page * 64 + i, data[i:i+2])
    return script + store(0x1000, [1]) + READY


def fuse_program(offset, value):
    return (READY + store(0x1008, [0x80 + offset]) + store(0x1009, [0x12]) +
            store(0x1006, [value]) + store(0x1000, [7]) + READY)


def record(address, data, kind=0):
    r = bytes([len(data), address >> 8, address & 255, kind]) + bytes(data)
    return ':' + (r + bytes([-sum(r) & 255])).hex().upper() + '\n'


class NvmTest(unittest.TestCase):
    # Reuse the existing PTY harness without inheriting its nine test cases.
    run_device = serial.SerialUpdiTest.run_device
    read_script = serial.SerialUpdiTest.read_script

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.start = self.read_script([0x1e, 0x91, 0x23])[:-len(CLEANUP)]
        self.blank = bytes([255]) * 64
        self.data = bytes(range(64))
        self.input = self.root / 'page.bin'
        self.input.write_bytes(self.data)

    def run_nvm(self, script, args, code=0, message=b'Operation completed', **kw):
        self.run_device(self.start + script + CLEANUP, code, message, args=args, **kw)

    def test_page_write_word_addresses_and_verify(self):
        self.run_nvm(page_read(31, self.blank) + program(31, self.data) + page_read(31, self.data),
                     ['--write-page', '31', str(self.input)], fragment=True)

    def test_identical_page_is_not_written(self):
        self.run_nvm(page_read(0, self.data), ['--write-page', '0', str(self.input)])

    def test_nonblank_page_rejected_before_write(self):
        self.run_nvm(page_read(0, bytes(64)), ['--write-page', '0', str(self.input)],
                     1, b'neither blank nor identical')

    def test_verify_failure_stops(self):
        self.run_nvm(page_read(0, self.blank) + program(0, self.data) + page_read(0, self.blank),
                     ['--write-page', '0', str(self.input)], 1, b'verify mismatch')

    def test_nvm_error_before_write(self):
        self.run_nvm([read(0x1002, 4)], ['--write-page', '0', str(self.input)], 1, b'NVM write error')

    def test_nvm_error_after_commit(self):
        script = page_read(0, self.blank) + program(0, self.data)[:-1] + [read(0x1002, 4)]
        self.run_nvm(script, ['--write-page', '0', str(self.input)], 1, b'NVM write error')

    def test_address_ack_failure_sends_no_data(self):
        script = page_read(0, self.blank) + READY + [packet('55 44 00 10', b'\x00')]
        self.run_nvm(script, ['--write-page', '0', str(self.input)], 1, b'address ACK rejected')

    def test_data_ack_failure_sends_no_flash_payload(self):
        script = page_read(0, self.blank) + READY + [packet('55 44 00 10', b'\x40'), packet('04', b'\x00')]
        self.run_nvm(script, ['--write-page', '0', str(self.input)], 1, b'data ACK rejected')

    def test_nvm_response_timeout_cleans_up(self):
        self.run_nvm([packet('55 04 02 10')], ['--write-page', '0', str(self.input)],
                     1, b'timeout waiting for serial response')

    def test_busy_then_ready(self):
        out = self.root / 'out.bin'
        self.run_nvm([read(0x1002, 3)] + page_read(0, self.data), ['--read-page', '0', str(out)])
        self.assertEqual(out.read_bytes(), self.data)

    def test_signature_mismatch_blocks_write(self):
        self.run_device(self.read_script([0x1e, 0x92, 0x23]), 2, b'Signature mismatch',
                        args=['--write-page', '0', str(self.input)])

    def test_hex_all_pages_preflight_before_any_write(self):
        f = self.root / 'in.hex'
        f.write_text(record(0, [1]) + record(64, [2]) + record(0, [], 1))
        self.run_nvm(page_read(0, self.blank) + page_read(1, bytes(64)), ['--write-hex', str(f)],
                     1, b'page 1 is neither blank nor identical')

    def test_hex_ff_padding_and_cross_page(self):
        f = self.root / 'in.hex'
        f.write_text(record(63, [1, 2]) + record(0, [], 1))
        a, b = self.blank[:63] + b'\x01', b'\x02' + self.blank[1:]
        script = page_read(0, self.blank) + page_read(1, self.blank)
        script += program(0, a) + page_read(0, a) + program(1, b) + page_read(1, b)
        self.run_nvm(script, ['--write-hex', str(f)])

    def test_hex_export_round_trip_and_last_address(self):
        f = self.root / 'out.hex'
        image = [self.blank] * 31 + [self.data]
        script = []
        for p, data in enumerate(image):
            script += page_read(p, data)
        self.run_nvm(script, ['--read-hex', str(f)])
        lines = f.read_text().splitlines()
        self.assertEqual(lines[-1], ':00000001FF')
        self.assertEqual(lines[-2], record(0x7f0, self.data[48:]).strip())
        # Import the exported image against identical target: no NVM writes.
        self.run_nvm(script, ['--write-hex', str(f)])

    def test_existing_output_is_preserved(self):
        self.run_nvm(page_read(0, self.blank), ['--read-page', '0', str(self.input)],
                     1, b'must not already exist')
        self.assertEqual(self.input.read_bytes(), self.data)

    def test_fuse_read_and_write(self):
        self.run_nvm(READY + [read(0x1286, 0xff)] + fuse_program(6, 0xf8) + READY + [read(0x1286, 0xf8)],
                     ['--write-fuse', 'SYSCFG1', 'F8', '--confirm-fuse'])

    def test_fuse_verify_failure(self):
        self.run_nvm(READY + [read(0x1286, 0xff)] + fuse_program(6, 0xf8) + READY + [read(0x1286, 0xff)],
                     ['--write-fuse', 'SYSCFG1', 'F8', '--confirm-fuse'], 1, b'fuse verify mismatch')

    def test_read_all_fuses_uses_named_offsets(self):
        script = []
        for offset in [0, 1, 2, 5, 6, 7, 8]:
            script += READY + [read(0x1280 + offset, 0xff)]
        self.run_nvm(script, ['--read-fuses'], message=b'BOOTEND = FF')

    def test_read_lock(self):
        self.run_nvm(READY + [read(0x128a, 0xc5)], ['--read-lock'], message=b'LOCKBIT = C5')

    def test_lock_is_explicit_verified_last_operation(self):
        self.run_nvm(READY + [read(0x128a, 0xc5)] + fuse_program(10, 0x3a) + READY + [read(0x128a, 0x3a)] + CLEANUP[:2] + [packet('55 8b', b'\x01')],
                     ['--lock', '--confirm-lock'])

    def test_erase_requires_eeprom_authorization(self):
        self.run_nvm(READY + [read(0x1285, 0xf6)], ['--chip-erase', '--confirm-erase'],
                     1, b'--allow-eeprom-erase')

    def erase_script(self, saved):
        script = READY + [read(0x1285, 0xf6 | saved)]
        script += block_read(0x1400, bytes(range(64)))
        script += READY + store(0x1000, [5]) + READY
        for p in range(32):
            script += page_read(p, self.blank)
        script += block_read(0x1400, bytes(range(64)) if saved else self.blank)
        return script + READY + [read(0x128a, 0xc5)]

    def test_chip_erase_preserves_eeprom_when_eesave(self):
        self.run_nvm(self.erase_script(True), ['--chip-erase', '--confirm-erase'])

    def test_chip_erase_explicit_eeprom_destruction(self):
        self.run_nvm(self.erase_script(False), ['--chip-erase', '--confirm-erase', '--allow-eeprom-erase'])

    def reject_cli(self, args, message=None):
        result = subprocess.run([str(APP), '/no-such-serial-port', *args], capture_output=True)
        self.assertEqual(result.returncode, 1, result)
        self.assertNotIn(b'open serial port', result.stderr)
        if message:
            self.assertIn(message, result.stderr)

    def test_invalid_cli_before_open(self):
        for args in [ ['--chip-erase'], ['--lock'], ['--confirm-lock'],
                      ['--read-lock', '--allow-eeprom-erase'], ['--read-lock', '--read-fuses'],
                      ['--read-page', '32', 'out'], ['--read-page', '-1', 'out'],
                      ['--write-fuse', 'low', 'FF', '--confirm-fuse'],
                      ['--write-fuse', 'SYSCFG0', 'FA', '--confirm-fuse'],
                      ['--write-fuse', 'SYSCFG0', 'F2', '--confirm-fuse'],
                      ['--write-fuse', 'SYSCFG1', '00', '--confirm-fuse'],
                      ['--write-fuse', 'OSCCFG', 'FF', '--confirm-fuse'],
                      ['--write-fuse', 'BODCFG', '20', '--confirm-fuse'],
                      ['--write-fuse', 'APPEND', '09', '--confirm-fuse'] ]:
            with self.subTest(args=args): self.reject_cli(args)

    def test_binary_length_before_open(self):
        for size in [0, 63, 65]:
            self.input.write_bytes(bytes(size))
            self.reject_cli(['--write-page', '0', str(self.input)], b'exactly 64')

    def test_invalid_hex_before_open(self):
        f = self.root / 'bad.hex'
        good = record(0, [1])
        eof = record(0, [], 1)
        for text in [good, eof, good[:-3] + '00\n' + eof, ':010000000GFF\n' + eof,
                     good + good + eof, good + eof + good, record(2048, [1]) + eof,
                     record(0, [0, 1], 4) + good + eof, record(0, [0, 0, 0, 0], 5) + eof,
                     record(1, [], 1), record(0, [1], 2) + good + eof]:
            with self.subTest(text=text):
                f.write_text(text)
                self.reject_cli(['--write-hex', str(f)])

    def test_busy_timeout_is_bounded_and_cleans_up(self):
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(APP), os.ttyname(slave), '--read-lock'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for expected, response in self.start:
                serial.SerialUpdiTest.exchange(self, master, expected, response)
            start = time.monotonic()
            while True:
                command = self.read_exact(master, 2)
                if command == bytes.fromhex('55 c8'):
                    command += self.read_exact(master, 1)
                    self.assertEqual(command, CLEANUP[0][0])
                    os.write(master, command)
                    break
                command += self.read_exact(master, 2)
                self.assertEqual(command, bytes.fromhex('55 04 02 10'))
                os.write(master, command + b'\x03')
            self.assertLess(time.monotonic() - start, 4)
            for expected, response in CLEANUP[1:]:
                serial.SerialUpdiTest.exchange(self, master, expected, response)
            out, err = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 1, (out, err))
            self.assertIn(b'NVM busy timeout', err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            os.close(slave)

    read_exact = serial.SerialUpdiTest.read_exact

    def test_flash_load_failure_never_commits_page(self):
        script = page_read(0, self.blank) + READY + store(0x1000, [4]) + READY
        script += [packet('55 45 00 80', b'\x40'), packet('00 01', b'\x00')]
        self.run_nvm(script, ['--write-page', '0', str(self.input)], 1, b'data ACK rejected')

    def test_first_page_verify_failure_does_not_program_second(self):
        f = self.root / 'in.hex'
        f.write_text(record(0, self.data) + record(64, self.data) + record(0, [], 1))
        script = page_read(0, self.blank) + page_read(1, self.blank)
        script += program(0, self.data) + page_read(0, self.blank)
        self.run_nvm(script, ['--write-hex', str(f)], 1, b'verify mismatch')

    def test_chip_erase_verify_failure(self):
        script = READY + [read(0x1285, 0xf7)]
        script += block_read(0x1400, bytes(range(64)))
        script += READY + store(0x1000, [5]) + READY + page_read(0, self.data)
        self.run_nvm(script, ['--chip-erase', '--confirm-erase'], 1, b'flash verification failed')

    def test_lock_readback_failure_reports_uncertainty(self):
        script = READY + [read(0x128a, 0xc5)] + fuse_program(10, 0x3a) + READY + [read(0x128a, 0xc5)]
        self.run_nvm(script, ['--lock', '--confirm-lock'], 1, b'lock state uncertain')

    def test_block_pointer_ack_failure_cleans_up(self):
        self.run_nvm(READY + [packet('55 69 00 80', b'\x00')],
                     ['--write-page', '0', str(self.input)], 1, b'pointer ACK rejected')

    def test_block_pointer_timeout_cleans_up(self):
        self.run_nvm(READY + [packet('55 69 00 80')],
                     ['--write-page', '0', str(self.input)], 1, b'timeout waiting for serial response')

    def test_block_echo_mismatch_each_phase_cleans_up(self):
        script = READY + block_read(0x8000, self.blank)
        for index in [1, 2, 3]:
            with self.subTest(index=index):
                failed = script[:index] + [(script[index][0], b'')]
                self.run_nvm(failed, ['--write-page', '0', str(self.input)], 1,
                             b'serial echo mismatch', corrupt_at=len(self.start) + index)

    def test_short_flash_block_never_writes_or_creates_output(self):
        script = READY + block_read(0x8000, self.blank)
        script[-1] = packet('55 24', self.blank[:63])
        self.run_nvm(script, ['--write-page', '0', str(self.input)], 1,
                     b'timeout waiting for serial response')
        out = self.root / 'short.bin'
        self.run_nvm(script, ['--read-page', '0', str(out)], 1,
                     b'timeout waiting for serial response')
        self.assertFalse(out.exists())

    def test_erase_checks_final_flash_byte(self):
        script = READY + [read(0x1285, 0xf7)] + block_read(0x1400, bytes(range(64)))
        script += READY + store(0x1000, [5]) + READY
        for p in range(31):
            script += page_read(p, self.blank)
        script += page_read(31, self.blank[:63] + b'\x00')
        self.run_nvm(script, ['--chip-erase', '--confirm-erase'], 1, b'flash verification failed')

    def test_erase_checks_final_eeprom_byte_preserved_and_erased(self):
        for saved in [False, True]:
            with self.subTest(saved=saved):
                script = self.erase_script(saved)
                # Last EEPROM LD response precedes READY and LOCKBIT reads.
                script[-3] = packet('55 24', (bytes(range(63)) if saved else self.blank[:63]) + b'\x00')
                self.run_nvm(script[:-2], ['--chip-erase', '--confirm-erase', '--allow-eeprom-erase'],
                             1, b'EEPROM verification failed')

    def test_hex_extended_zero_base(self):
        f = self.root / 'ext.hex'
        for kind in (2, 4):
            with self.subTest(kind=kind):
                f.write_text(record(0, [0, 0], kind) + record(0, self.data) + record(0, [], 1))
                self.run_nvm(page_read(0, self.data), ['--write-hex', str(f)])
