# kube-triage

Kubernetes の Pod 障害を**一次切り分け**する Python CLI です。
Pod の状態・イベント・ログを読み取り、障害の原因を判定して、**判定根拠**・推奨対応・確認コマンドを日本語で出力します。

判定は 3 つの方式から選べます。

| 方式 | 内容 |
|---|---|
| `rules` | ルールベース。標準ライブラリのみで動き、API 費用はかからない |
| `llm` | Claude API（Anthropic）が判定する |
| `hybrid` | まずルールで判定し、**ルールで原因を確定できないときだけ** Claude に判定させる |

評価専用のシナリオ（ルール・プロンプトの修正に使っていない 14 件）では、原因まで含めた正解率が
**ルールのみ 50% → ハイブリッド（Claude Haiku 5.5）86%** に向上し、費用は **Pod 100 件あたり約 $0.03** でした。

```
$ kube-triage analyze scenarios/17_crash_invalid_config.json --engine hybrid --model claude-haiku-5-5
Pod: shop/api-badflag  (phase: Running)
判定: CRASH_LOOP / invalid_config  [確度 90%]
  設定値 log_level の不正により起動直後に異常終了を繰り返している
根拠:
  - [status.containerStatuses[app].state.waiting.reason] CrashLoopBackOff
  - [status.containerStatuses[app].lastState.terminated] exitCode: 2, reason: Error, restartCount: 4
  - [logs/app] level=fatal msg="invalid value for log_level: 'verbose' (expected one of debug, info, warn, error)"
推奨対応:
  1. /etc/api/config.yaml の log_level を debug, info, warn, error のいずれかに修正する
  2. 修正した ConfigMap または設定ファイルを反映し、Pod を再作成または再起動して起動を確認する
```

## 背景

OpenShift / Kubernetes 基盤の運用では、Pod が起動しないときに `describe`・`get events`・`logs --previous` を順に確認し、
経験をもとに原因を絞り込む作業が繰り返し発生します。
この「人が目で見て判断している手順」をコード化し、**誰が実行しても同じ観点で、根拠付きの一次切り分けができる**ことを目指しました。

設計で重視した点:

- **判定根拠の追跡**: どのフィールド・イベント・ログ行から判断したかを必ず出力し、結果を鵜呑みにせず検証できるようにする（LLM にも入力からの引用を根拠として出力させる）
- **評価による改善**: 正解ラベル付きのシナリオ集で正解率を CI で毎回測定し、改善の効果を数値で確認する
- **費用を抑えた LLM の使い方**: 確実に判定できるものはルールで処理し、LLM は取りこぼしだけに使う。モデルごとの精度と費用を比較して選ぶ
- **再現性**: LLM の応答をキャッシュしてコミットし、API キーがなくても同じ評価結果を再現できる

## 判定できる障害

| カテゴリ | 原因 (subcause) | ルールの主な判定材料 |
|---|---|---|
| `OOM_KILLED` | — | `lastState.terminated.reason`、メモリ limits |
| `IMAGE_PULL` | `image_not_found` / `registry_auth` | `waiting.reason`、Failed イベントのメッセージ |
| `CONFIG_ERROR` | `missing_configmap` / `missing_secret` / `missing_key` | `CreateContainerConfigError` のメッセージ |
| `UNSCHEDULABLE` | `insufficient_resources` / `pvc_unbound` / `taint` / `node_selector` | `PodScheduled` 条件、FailedScheduling イベント |
| `PROBE_FAILURE` | `liveness` / `readiness` | Unhealthy / Killing イベント |
| `CRASH_LOOP` | `missing_env` / `dependency_unreachable` / `permission` / `bad_command` / `invalid_config` | ログ・終了メッセージのパターン、終了コード |

ルールは複数が該当した場合に確度の高い順に並べます。
例えば `CrashLoopBackOff` でも直前の終了理由が `OOMKilled` なら OOM を主判定にし、
終了コード 137 でも Liveness Probe による kill のイベントがあれば Probe 失敗を主判定にします。
`invalid_config` はルールでは判定せず、LLM のみが判定します。

## アーキテクチャ

```
 Kubernetes ──(kubectl)──> collect ──> Bundle(JSON) ──> rules ──┬──> report (テキスト / JSON)
                                                                │
                                         原因を確定できない場合 └──> llm (Claude API) ─┘
                                                                        │
                                                         llm-cache/ (応答のキャッシュ)

 scenarios*/ (正解ラベル付き) ──> eval (正解率・費用の測定, CI で毎回実行)
```

| モジュール | 役割 |
|---|---|
| `collect.py` | `kubectl` で Pod / イベント / ログ（initContainer を含む）を取得し、Bundle(JSON) として保存 |
| `rules.py` | ルールベースの判定。各ルールが `Finding`（カテゴリ・根拠・対応）を返す |
| `llm.py` | Claude による判定。入力の圧縮、JSON スキーマで出力形式を固定、応答キャッシュ、使用量・費用の集計 |
| `evaluate.py` | 正解ラベル付きシナリオ集に対する正解率の算出 |
| `report.py` | 判定結果をテキスト / JSON で出力 |

LLM には Pod 全体ではなく、判定に必要なフィールド（状態・条件・イベント・ログ末尾 40 行など）だけを渡して入力トークンを抑えています。
出力は JSON スキーマ（Structured Outputs）でカテゴリ・原因の選択肢を固定し、ルールと同じ `Finding` に変換するため、
評価・レポートの仕組みを共通で使えます。

## 使い方

```bash
git clone https://github.com/johann-wg/kube-triage.git
cd kube-triage
pip install -e ".[llm]"        # ルールのみなら pip install -e .

# 1. 実クラスタから収集 (kubectl が使える環境)
kube-triage collect api-7d9f -n shop -o bundle.json

# 2. 診断
kube-triage analyze bundle.json                                        # ルールのみ
kube-triage analyze bundle.json --engine hybrid --model claude-haiku-5-5   # 要 ANTHROPIC_API_KEY
kube-triage analyze bundle.json --json                                 # 他ツール連携用

# 3. シナリオ集で精度を評価
kube-triage eval scenarios-holdout
kube-triage eval scenarios-holdout --engine hybrid --model claude-haiku-5-5 --offline   # キャッシュのみ・API 不要
```

`--engine llm|hybrid` を使うと、実行後に API 呼び出し回数・トークン数・費用が表示されます。
`--model` を省略した場合は `claude-opus-5-5` を使います。

## 評価結果

| シナリオ集 | 件数 | 用途 |
|---|---|---|
| `scenarios-holdout/` | 14 | **評価専用**。ルール・プロンプトの修正には使わない。正解ラベルは判定の実行前に決めて固定 |
| `scenarios-real/` | 15 | kind クラスタで実際に障害を再現して収集したデータ |
| `scenarios/` | 18 | 合成データ。境界ケース（認証エラー、taint など）を含む |

### 評価専用シナリオでの比較（主な結果）

| 方式 | カテゴリ | カテゴリ + 原因 | Pod 100 件あたり費用 |
|---|---|---|---|
| ルールのみ | 12/14 (86%) | 7/14 (50%) | $0 |
| ハイブリッド（Claude Haiku 5.5） | 13/14 (93%) | 12/14 (86%) | 約 $0.03 |
| ハイブリッド（Claude Sonnet 5.5） | 14/14 (100%) | 12/14 (86%) | 約 $0.57 |
| ハイブリッド（Claude Opus 5.5） | 14/14 (100%) | 13/14 (93%) | 約 $1.25 |
| LLM のみ（Claude Opus 5.5） | 14/14 (100%) | 13/14 (93%) | 約 $1.71 |

全組み合わせは [docs/engine-comparison.md](docs/engine-comparison.md) を参照してください（`scripts/compare_engines.py` で生成）。
ハイブリッドは 14 件中 10 件だけ LLM を呼ぶため、同じモデルの LLM のみより約 3 割安く、精度は同じでした。

分かったこと:

- ルールは既知の文言には強い一方、`UnknownHostException`、`i/o timeout`、`Read-only file system` など**表現が変わると原因を判定できない**（評価専用で 50%）
- Haiku は安価だが、**JVM のヒープ不足（アプリ自身の終了）をコンテナの OOMKilled と誤判定**し、x509 証明書エラーを認証エラーに分類した
- Sonnet と Opus は、`ModuleNotFoundError`（イメージに依存パッケージがない）を定義済みの原因 `bad_command` に当てはめた（Haiku は「該当なし」と正しく回答）。Sonnet は JVM のヒープ不足も `invalid_config` に分類した。分類外の原因を「該当なし」と答えさせるのは今後の課題
- 費用対効果では Haiku のハイブリッドが最も良く、Opus は Haiku の約 40 倍の費用で原因の正解が 1 件多い

評価専用シナリオの結果を見てプロンプトを調整すると評価の意味がなくなるため、上記の誤りは修正せずに記録しています。
改善する場合は、新しい評価専用シナリオを別途用意して測り直します。

### 実データ・合成データ

| シナリオ集 | ルールのみ（カテゴリ + 原因） | ハイブリッド Haiku（カテゴリ + 原因） |
|---|---|---|
| 実データ（15 件） | 15/15 (100%) | 15/15 (100%) |
| 合成データ（18 件） | 17/18 (94%) | 18/18 (100%) |

### 実データで見つかった問題と改善

最初は合成データだけでルールを作り、その後 [lab/](lab/README.md) で実際に障害を再現して評価しました。
実データでの初回の結果は **カテゴリ + 原因 10/15（66.7%）** で、合成データでは見えなかった次の問題が分かりました。

| 問題 | 原因 | 対応 |
|---|---|---|
| CrashLoop の原因がほぼ判定できない | 古いコンテナが削除済みだと `kubectl logs --previous` が終了コード 0 のまま `unable to retrieve container logs` を返し、エラーログを取得できていなかった | 直前と現在の両方のログを取得し、本文でも取得失敗を判定 |
| 存在しないコマンドを判定できない | 実際は終了コード 127 ではなく 128（`StartError`）で、ログは空。原因は `lastState.terminated.message` にだけ残る | ログに加えて終了メッセージ・待機メッセージも判定材料にする |
| initContainer の失敗が UNKNOWN になる | `initContainerStatuses` を見ていなかった | initContainer を先に判定し、ログも収集する |

改善後は 15/15 になりましたが、ルール修正に使ったデータでの結果です。未知の障害に対する精度は、上記の評価専用シナリオで測っています。

### シナリオデータについて

- `scenarios/`: [scripts/generate_scenarios.py](scripts/generate_scenarios.py) で生成した合成データ
- `scenarios-real/`: ローカルの kind クラスタで [lab/manifests/](lab/manifests/) の障害を再現し、[scripts/collect_lab.py](scripts/collect_lab.py) で収集したデータ
- `scenarios-holdout/`: [scripts/generate_holdout.py](scripts/generate_holdout.py) で生成した評価専用データ
- `llm-cache/`: 評価で得た Claude の応答。入力（モデル・effort・プロンプト版・圧縮した Bundle）のハッシュをキーに保存

業務で扱った環境のデータは一切含みません。

## ロードマップ

- [x] ルールベース判定 6 カテゴリ・評価シナリオ 18 件・CI
- [x] kind クラスタで障害を再現するマニフェスト 15 件と収集スクリプト（[lab/](lab/README.md)）
- [x] 実データで再評価し、合成データとの差分をルールと収集処理に反映
- [x] initContainer の判定に対応
- [x] 評価専用シナリオ（holdout）の追加
- [x] Claude API による判定とハイブリッド方式、モデル別の精度・費用比較
- [ ] 分類外の原因を「該当なし」と答えさせる改善と、そのための新しい評価専用シナリオ

## 開発

```bash
pip install -e ".[dev,llm]"
pytest -q                      # API は呼ばない (キャッシュのみ使用)
ruff check . && ruff format --check .
```

## ライセンス

MIT
