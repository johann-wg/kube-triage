# 障害再現ラボ (kind)

`manifests/` の Pod をローカルの kind クラスタに適用し、実際の障害データを収集して評価します。
各 Pod の annotation `triage-lab/expected` が正解ラベルです。

## 必要なもの

- Docker Desktop
- [kind](https://kind.sigs.k8s.io/) と kubectl

## 手順

```bash
kind create cluster --name triage-lab
kubectl apply -f lab/namespace.yaml
kubectl apply -f lab/manifests/

# 障害が安定するまで数分待つ (CrashLoopBackOff は再起動を繰り返すまで時間がかかる)
kubectl -n triage-lab get pods -w

python scripts/collect_lab.py           # scenarios-real/ に保存
kube-triage eval scenarios-real         # 実データで評価

kind delete cluster --name triage-lab   # 後片付け
```

## 合成データとの違いを確認するポイント

- `12-crash-bad-command`: 存在しないコマンドはログが出ず、終了理由が `StartError`、メッセージが `lastState.terminated.message` に入る可能性がある
- `08-liveness-probe`: 終了コードやイベントの文言が合成データと同じか
- `14-init-failure`: `initContainerStatuses` に現れる (現行ルールは未対応)

合成データでは再現しにくい `registry_auth` (認証エラー) と `taint` はこのラボの対象外です。
