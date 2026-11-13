# ATtiny202 シグネチャ読み出し

SerialUPDI 経由で ATtiny202 のシグネチャを取得する、Linux 向けの C++ プログラムです。
`avrdude -c serialupdi -P /dev/ttyUSB0 -p attiny202 -v` のシグネチャ確認に相当する処理を、シリアルポートを直接操作して行います。実行時に avrdude は不要です。

ATtiny202 の実機で、`0x1E9123` の読み出しを確認済みです。シグネチャは型番を識別する値で、個体固有のシリアル番号ではありません。

## 必要な環境

- Linux
- C++17 対応の `g++` と GNU Make
- テストを実行する場合は Python 3（標準ライブラリのみ使用）
- SerialUPDI 回路を備えた USB–UART アダプターと、電源を供給した ATtiny202
- 使用するシリアルポートへの読み書き権限

avrdude の `serialupdi` で通信できている接続を利用できます。通常の UART の TX/RX 接続ではなく、UPDI の単線通信と送信エコーに対応した回路が必要です。ターゲットとアダプターの GND を共通にし、信号電圧を合わせてください。

## ビルドと実行

```bash
make
./myapp
```

デフォルトのポートは `/dev/ttyUSB0` です。ポート指定と通信ログの表示は次のように行います。

```bash
./myapp /dev/ttyUSB1
./myapp -v /dev/ttyUSB0
./myapp --help
```

成功時の出力：

```text
Device signature = 0x1E9123
ATtiny202 detected
```

`-v` を指定すると、送信データ `TX`、送信エコー `ECHO`、ターゲットからの応答 `RX` を16進数で標準エラー出力に表示します。結果は標準出力、エラーは標準エラー出力に表示します。

終了コード：

| 値 | 意味 |
| --- | --- |
| `0` | ATtiny202 の識別に成功（ヘルプ表示時も `0`） |
| `1` | 引数、ポート操作、通信、SIB、モード移行、終了処理などのエラー |
| `2` | 読み出したシグネチャが ATtiny202 の期待値と不一致 |

## 通信処理

1. シリアルポートを 115200 bps・8E2（8ビット、偶数パリティ、2ストップビット）、フロー制御なしで開きます。
2. UPDI リンクを初期化します。初回接続に失敗した場合は、300 bps・8E1 で double BREAK を送信して再試行します。
3. 32バイトの SIB を取得し、`tinyAVR` ファミリーと NVM インターフェースのバージョン `0` を確認します。`P:0` のようなフィールドのコロン後を判定します。
4. NVM プログラミングキーを送信し、プログラミングモードへの移行を確認します。
5. `0x1100`、`0x1101`、`0x1102` から1バイトずつ読み出します。
6. リセット要求・解除と UPDI 無効化を行い、ポート設定の復元を試みて閉じます。

部分的な読み書き、送信エコーの照合、タイムアウトに対応しています。送信とエコー受信の共通期限、および応答受信の期限はそれぞれ1秒です。モード移行のポーリング期限は500ミリ秒ですが、個々の通信待ちにより全体ではそれを超える場合があります。

現在の対象は ATtiny202 です。Flash・EEPROM・ヒューズの書き込みやチップ消去は実装していません。ロック状態で読み出せない場合はエラーになります。読み出し操作でもターゲットはリセットされます。

## テスト

```bash
make test
```

Python の疑似端末（PTY）でターゲットの応答を模擬します。実機は不要です。正常な読み出しと受信の分割、シグネチャ不一致、不正・未対応の SIB、キー拒否、エコー不一致、ロック状態、応答タイムアウト、double BREAK による復帰を確認します。

PTY テストでは実際の電気的特性や UART のパリティ・ストップビットの動作は検証できません。通信設定やコマンド列を変更した場合は、実機でも確認してください。

## Makefile の主なターゲット

| ターゲット | 内容 |
| --- | --- |
| `make` | `myapp` をビルド |
| `make run` | ビルド済みの `myapp` をデフォルト設定で実行 |
| `make test` | ビルド後、疑似端末テストを実行 |
| `make dude` | 比較用の avrdude コマンドを実行（avrdude が必要） |
| `make clean` | 実行ファイル、オブジェクト、依存関係ファイルを削除 |
| `make install` | ビルド済みの `myapp` を `~/.local/bin/` に配置 |
| `make uninstall` | `~/.local/bin/myapp` を削除 |

`run` と `install` はビルドを行わないため、先に `make` を実行してください。`install` の配置先ディレクトリは事前に用意する必要があります。

## 問題が起きた場合

- ポートを開けない場合は、デバイスパスとアクセス権、ほかのアプリケーションによる使用状況を確認してください。
- エコー不一致やタイムアウトの場合は、SerialUPDI 回路、配線、電源、GND、信号電圧を確認してください。
- `unsupported SIB` の場合は、エラーに表示された SIB と `-v` の受信ログを確認してください。
- avrdude と比較する場合は、同じ回路とポートで `make dude` を実行してください。同時に両プログラムを起動しないでください。

## ファイル構成

- `main.cpp`：シリアル通信、UPDI 制御、コマンドライン処理
- `main.h`：現時点では未使用のヘッダーファイル
- `Makefile`：ビルド・実行・テスト
- `tests/test_serialupdi.py`：疑似端末による通信テスト
- `AGENTS.md`：開発エージェント向けの作業指針

## 参考資料

- [avrdude の SerialUPDI 説明](https://avrdudes.github.io/avrdude/8.0/avrdude_22.html)
- [avrdude の SerialUPDI 制御](https://github.com/avrdudes/avrdude/blob/main/src/serialupdi.c)
- [avrdude の UPDI 通信層](https://github.com/avrdudes/avrdude/blob/main/src/updi_link.c)
- [avrdude のデバイス定義](https://github.com/avrdudes/avrdude/blob/main/src/avrdude.conf.in)
