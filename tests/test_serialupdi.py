"""Protocol integration tests using a PTY; no device or extra packages needed."""
import os
import pathlib
import pty
import select
import subprocess
import time
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / 'myapp'


def packet(tx, rx=b''):
    return bytes.fromhex(tx), rx


LINK = [packet('55 c3 08'), packet('55 c2 80'), packet('55 80', b'\x10')]
CLEANUP = [packet('55 c8 59'), packet('55 c8 00'), packet('55 c3 0c')]
SIB = b'tinyAVR P:0D:0-3M2 (01.59B14.0)'.ljust(32, b'\x00')
INIT = ([packet('00')] + LINK + [packet('55 8b', b'\x00'),
        packet('55 e6', SIB)] + LINK)
KEY = [packet('55 8b', b'\x00'), packet('55 c8 59'), packet('55 e0'),
       packet('20 67 6f 72 50 4d 56 4e'), packet('55 87', b'\x10'),
       packet('55 c8 59'), packet('55 c8 00')]


class SerialUpdiTest(unittest.TestCase):
    def run_device(self, script, code, contains, fragment=False, corrupt_at=None, args=(), app=APP, port_option='-p'):
        master, slave = pty.openpty()
        port_args = [port_option, os.ttyname(slave)] if app == APP else [os.ttyname(slave)]
        proc = subprocess.Popen([str(app), *port_args, *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for index, (expected, response) in enumerate(script):
                actual = b''
                deadline = time.monotonic() + 4
                while len(actual) < len(expected):
                    remaining = deadline - time.monotonic()
                    self.assertGreater(remaining, 0, f'waiting for {expected.hex()}')
                    ready, _, _ = select.select([master], [], [], remaining)
                    self.assertTrue(ready, f'waiting for {expected.hex()}')
                    actual += os.read(master, len(expected) - len(actual))
                self.assertEqual(actual, expected)
                echo = actual
                if index == corrupt_at:
                    echo = bytes([actual[0] ^ 0xff]) + actual[1:]
                data = echo + response
                if fragment:
                    for value in data:
                        os.write(master, bytes([value]))
                        time.sleep(0.001)
                else:
                    os.write(master, data)
            out, err = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, code, (out, err))
            self.assertIn(contains, out + err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            os.close(slave)

    def read_script(self, signature):
        return (INIT + KEY + [packet('55 8b', b'\x00'),
                packet('55 8b', b'\x08'), packet('55 8b', b'\x08')] +
                [packet(f'55 04 {i:02x} 11', bytes([value]))
                 for i, value in enumerate(signature)] + CLEANUP)

    def test_signature_and_fragmented_reads(self):
        for option in ['-p', '--port']:
            with self.subTest(option=option):
                self.run_device(self.read_script([0x1e, 0x91, 0x23]), 0,
                                b'Device signature = 0x1E9123', fragment=True, port_option=option)

    def test_invalid_port_options_before_open(self):
        for args in [['-p'], ['--port'], ['-p', ''], ['--port', '--read-lock'],
                     ['-p', '/no-such-port', '--port', '/other-port'], ['/no-such-port']]:
            with self.subTest(args=args):
                result = subprocess.run([str(APP), *args], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(b'open serial port', result.stderr)

    def test_signature_mismatch(self):
        self.run_device(self.read_script([0x1e, 0x92, 0x23]), 2,
                        b'Signature mismatch')

    def test_unsupported_nvm_reports_received_sib(self):
        sib = SIB[:10] + b'2' + SIB[11:]
        self.run_device(INIT[:5] + [packet('55 e6', sib)] + CLEANUP,
                        1, b'received: "tinyAVR P:2D:0-3M2 (01.59B14.0)\\x00"')

    def test_malformed_nvm_field(self):
        sib = SIB[:9] + b'?' + SIB[10:]
        self.run_device(INIT[:5] + [packet('55 e6', sib)] + CLEANUP,
                        1, b'unsupported SIB')

    def test_rejected_key_still_cleans_up(self):
        self.run_device(INIT + KEY[:4] + [packet('55 87', b'\x00')] + CLEANUP,
                        1, b'NVM programming key rejected')

    def test_echo_mismatch_still_cleans_up(self):
        self.run_device(INIT + [packet('55 8b')] + CLEANUP,
                        1, b'serial echo mismatch', corrupt_at=len(INIT))

    def test_locked_device(self):
        # More replies than fit within the 500 ms polling window are unnecessary:
        # run_device checks a script, so supply replies dynamically in this case.
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(APP), '--port', os.ttyname(slave)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            script = INIT + KEY
            for expected, response in script:
                self.exchange(master, expected, response)
            # The next non-status command must be the cleanup reset.
            while True:
                command = self.read_exact(master, 2)
                if command == bytes.fromhex('55 c8'):
                    command += self.read_exact(master, 1)
                    self.assertEqual(command, CLEANUP[0][0])
                    os.write(master, command)
                    break
                self.assertEqual(command, bytes.fromhex('55 8b'))
                os.write(master, command + b'\x01')
            for expected, response in CLEANUP[1:]:
                self.exchange(master, expected, response)
            out, err = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 1, (out, err))
            self.assertIn(b'device is locked', err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            os.close(slave)

    def read_exact(self, fd, count):
        data = b''
        while len(data) < count:
            ready, _, _ = select.select([fd], [], [], 4)
            self.assertTrue(ready, 'protocol timeout')
            data += os.read(fd, count - len(data))
        return data

    def exchange(self, fd, expected, response):
        self.assertEqual(self.read_exact(fd, len(expected)), expected)
        os.write(fd, expected + response)

    def test_response_timeout_still_cleans_up(self):
        self.run_device(INIT + KEY + [packet('55 8b')] + CLEANUP,
                        1, b'timeout waiting for serial response')

    def test_double_break_recovers_inactive_link(self):
        # Invalid STATUSA triggers recovery; validate both low-speed zero bytes.
        initial = [packet('00')] + LINK[:2] + [packet('55 80', b'\x00')]
        recovered = [packet('00'), packet('00')] + self.read_script([0x1e, 0x91, 0x23])[1:]
        self.run_device(initial + recovered, 0, b'ATtiny202 detected')


if __name__ == '__main__':
    unittest.main()
