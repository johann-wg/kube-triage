# kube-triage

Kubernetes の Pod 障害を**一次切り分け**する Python CLI です。
Pod の状態・イベント・ログを読み取り、障害の原因カテゴリを判定して、**判定根拠**・推奨対応・確認コマンドを日本語で出力します。

```
$ kube-triage analyze scenarios/02_oom_killed.json
Pod: shop/api-oom  (phase: Running)
判定: OOM_KILLED  [確度 95%]
  メモリ上限超過によりコンテナが強制終了されています
根拠:
  - [status.containerStatuses[app].lastState] terminated.reason=OOMKilled, exitCode=137
  - [spec.containers[app].resources.limits] memory=128Mi
推奨対応:
  1. resources.limits.memory が実際の使用量に対して小さすぎないか確認する
  2. メモリ使用量の推移を確認し、リーク（右肩上がり）か一時的なピークかを切り分ける
  3. JVM 等はヒープ上限をコンテナの limits に合わせて設定する
確認コマンド:
  $ kubectl -n shop describe pod api-oom
  $ kubectl -n shop top pod api-oom --containers
その他の候補:
  - CRASH_LOOP [確度 60%] コンテナが起動直後に異常終了し、再起動を繰り返しています
```

## 背景

OpenShift / Kubernetes 基盤の運用では、Pod が起動しないときに `describe`・`get events`・`logs --previous` を順に確認し、
経験をもとに原因を絞り込む作業が繰り返し発生します。
この「人が目で見て判断している手順」をコード化し、**誰が実行しても同じ観点で、根拠付きの一次切り分けができる**ことを目指しました。

設計で重視した点:

- **判定根拠の追跡**: どのフィールド・イベント・ログ行から判断したかを必ず出力し、結果を鵜呑みにせず検証できるようにする
- **評価による改善**: 障害シナリオ集に対する正解率を CI で毎回測定し、ルール追加の効果を数値で確認する
- **依存ライブラリなし**: 標準ライブラリのみで実装し、踏み台サーバーなど制約のある環境でも動かせるようにする

## 判定できる障害

| カテゴリ | 原因 (subcause) | 主な判定材料 |
|---|---|---|
| `OOM_KILLED` | — | `lastState.terminated.reason`、メモリ limits |
| `IMAGE_PULL` | `image_not_found` / `registry_auth` | `waiting.reason`、Failed イベントのメッセージ |
| `CONFIG_ERROR` | `missing_configmap` / `missing_secret` / `missing_key` | `CreateContainerConfigError` のメッセージ |
| `UNSCHEDULABLE` | `insufficient_resources` / `pvc_unbound` / `taint` / `node_selector` | `PodScheduled` 条件、FailedScheduling イベント |
| `PROBE_FAILURE` | `liveness` / `readiness` | Unhealthy / Killing イベント |
| `CRASH_LOOP` | `missing_env` / `dependency_unreachable` / `permission` / `bad_command` | 直前ログのパターン、終了コード (126 / 127) |

複数のルールが該当した場合は確度の高い順に並べます。
例えば `CrashLoopBackOff` でも直前の終了理由が `OOMKilled` なら OOM を主判定にし、
終了コード 137 でも Liveness Probe による kill のイベントがあれば Probe 失敗を主判定にします。

## アーキテクチャ

```
 Kubernetes ──(kubectl)──> collect ──> Bundle(JSON) ──> rules ──> report (テキスト / JSON)
                                                          ^
               scenarios/ + scenarios-real/ ──> eval (正解率の測定, CI で毎回実行)
```

| モジュール | 役割 |
|---|---|
| `collect.py` | `kubectl` で Pod / イベント / ログを取得し、Bundle(JSON) として保存 |
| `bundle.py` | Bundle の読み込みと、Pod 情報へのアクセス |
| `rules.py` | ルールベースの判定。各ルールが `Finding` (カテゴリ・根拠・対応) を返す |
| `report.py` | 判定結果をテキスト / JSON で出力 |
| `evaluate.py` | 正解ラベル付きシナリオ集に対する正解率の算出 |

## 使い方

```bash
git clone https://github.com/johann-wg/kube-triage.git
cd kube-triage
pip install -e .

# 1. 実クラスタから収集 (kubectl が使える環境)
kube-triage collect api-7d9f -n shop -o bundle.json

# 2. 診断
kube-triage analyze bundle.json
kube-triage analyze bundle.json --json   # 他ツール連携用

# 3. シナリオ集で精度を評価
kube-triage eval scenarios
```

## 評価結果

2 種類のシナリオ集で判定精度を測定しています（どちらも CI で毎回実行）。

| シナリオ集 | 件数 | カテゴリ正解率 | カテゴリ + 原因 |
|---|---|---|---|
| 実データ（kind で障害を再現して収集） | 15 | 100.0% | 100.0% |
| 合成データ（境界ケースを含む） | 18 | 100.0% | 94.4% |

詳細: [実データ](docs/eval-real.md) / [合成データ](docs/eval-synthetic.md)

### 実データで見つかった問題と改善

最初は合成データだけでルールを作り、その後 [lab/](lab/README.md) で実際に障害を再現して評価しました。
実データでの初回の結果は **カテゴリ + 原因 10/15（66.7%）** で、合成データでは見えなかった次の問題が分かりました。

| 問題 | 原因 | 対応 |
|---|---|---|
| CrashLoop の原因がほぼ判定できない | 古いコンテナが削除済みだと `kubectl logs --previous` が終了コード 0 のまま `unable to retrieve container logs` を返し、エラーログを取得できていなかった | 直前と現在の両方のログを取得し、本文でも取得失敗を判定 |
| 存在しないコマンドを判定できない | 実際は終了コード 127 ではなく 128（`StartError`）で、ログは空。原因は `lastState.terminated.message` にだけ残る | ログに加えて終了メッセージ・待機メッセージも判定材料にする |
| initContainer の失敗が UNKNOWN になる | `initContainerStatuses` を見ていなかった | initContainer を先に判定し、ログも収集する |

改善後は 15/15 になりましたが、**ルール修正に使ったデータと評価データが同じ**であるため、未知の障害に対する精度を示すものではありません。
今後は修正に使っていない障害シナリオを追加して評価します。

### 既知の取りこぼし

| シナリオ | 内容 | 取りこぼす理由 |
|---|---|---|
| `17_crash_invalid_config` | 設定値の誤りで異常終了 | ログの文言がアプリ固有で、汎用パターンに当たらない |

### シナリオデータについて

- `scenarios/`: [scripts/generate_scenarios.py](scripts/generate_scenarios.py) で生成した合成データ。実データでは再現しにくい境界ケース（認証エラー、taint など）を含む
- `scenarios-real/`: ローカルの kind クラスタで [lab/manifests/](lab/manifests/) の障害を再現し、[scripts/collect_lab.py](scripts/collect_lab.py) で収集したデータ

業務で扱った環境のデータは一切含みません。

## ロードマップ

- [x] ルールベース判定 6 カテゴリ・評価シナリオ 18 件・CI
- [x] kind クラスタで障害を再現するマニフェスト 15 件と収集スクリプト（[lab/](lab/README.md)）
- [x] 実データで再評価し、合成データとの差分をルールと収集処理に反映
- [x] initContainer の判定に対応
- [ ] ルール修正に使っていない障害シナリオ（評価専用）を追加
- [ ] ルールで判定できないケースのみ LLM に原因推定させ、ルールのみの場合と正解率を比較

## 開発

```bash
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

## ライセンス

MIT
