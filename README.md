# SAT Scheduler MVP

海外在住の講師・生徒を含むSATオンライン講義向けの日程調整MVPです。

## 実装済み

- ログインID・パスワードによる認証（PBKDF2-SHA256でハッシュ保存）
- ログイン画面から講師・生徒が利用できる新規アカウント登録
- 「塾長・管理者・講師・生徒」の4役割によるアクセス制御
- 塾長・管理者も管理権限を維持したまま担当講師を兼任可能
- 兼任する塾長・管理者は、ヘッダーから管理者権限と講師権限を切り替え可能
- 塾長・管理者による利用者登録、ログインID変更、パスワード再設定
- 利用者画面から講師を管理者へ変更（担当中の授業は保持）
- IANAタイムゾーン（例: `Asia/Tokyo`, `America/New_York`）
- 日本語を含む地名検索からのタイムゾーン自動設定
- 講師が曜日ごとの通常授業可能時間と例外予定を設定
- 講師の授業可能時間外にある候補日時の登録・確定を防止（範囲未設定時は制限なし）
- 管理者が日本時間で候補日時を提示し、講師と生徒がそれぞれ回答
- 講師または生徒が選択したタイムゾーンの現地時刻で候補日時を提示
- 未確定の候補日時を個別に取り消し、変更履歴へ記録
- UTCで内部保存し、JST＋双方の現地時間を併記
- 管理者の候補は双方の承諾、講師・生徒の候補は相手側の承諾で自動確定
- 確定授業との重複除外
- 変更履歴（audit log）
- 確定後に担当講師がZoom参加リンク・ミーティングID・パスワードを任意登録
- 確定した講義情報をLINE・メール向けのテキストとしてワンクリックでコピー

## 起動方法

### Docker + PostgreSQLで起動する場合

AWS本番環境と同じデータベース構成をローカルで確認できます。

```bash
docker compose up --build
```

起動後、`http://127.0.0.1:8000` を開き、`/setup` から最初の塾長を登録します。
データはDockerの `postgres_data` ボリュームへ保存されます。

### 初回ログイン

デモデータを投入していない場合、または認証導入前のDBを使っている場合は、最初に
`/setup` が表示されます。ここで最初の「塾長」アカウントを作成してください。既存の
講師・生徒データは保持され、ログイン情報は塾長・管理者が「利用者」画面から設定できます。

`python seed.py` で新しいデモDBを作成した場合のログイン情報は次のとおりです。

- 塾長: `owner` / `owner1234`
- 管理者: `admin` / `admin1234`
- 講師: `instructor` / `teacher123`
- 生徒: `student` / `student123`

本番環境では固定のセッション署名鍵を環境変数
`SAT_SCHEDULER_SESSION_SECRET` に設定し、HTTPS運用時は
`SAT_SCHEDULER_HTTPS_ONLY=1` も設定してください。

## Renderへのデプロイ

ルートの [`render.yaml`](render.yaml) は、Docker Web ServiceとRender Postgresを
同じシンガポールリージョンへ作成するBlueprintです。セッション署名鍵とDB接続URLは
Renderが自動設定し、PostgreSQLへの外部ネットワーク接続は許可しません。

1. このディレクトリをGitHubまたはGitLabのリポジトリへpushします。
2. Render Dashboardで **New > Blueprint** を選び、リポジトリを接続します。
3. `render.yaml` の内容を確認してBlueprintを作成します。
4. Web Serviceのデプロイ完了後、発行されたURLを開きます。
5. `/setup` から最初の塾長アカウントを登録します。

Web ServiceとPostgreSQLは費用が発生しない `free` プランを明示しています。無料Web Serviceは
15分間アクセスがないと停止し、次回起動に約1分かかります。無料PostgreSQLは作成30日後に
失効し、バックアップもないため、検証用途に限ってください。本番運用では `render.yaml` の
Web Serviceを `starter` 以上、PostgreSQLを `basic-256mb` 以上へ変更してください。
ヘルスチェックにはDB接続も確認する `/readyz` を使います。


### 地名からタイムゾーンを検索する場合

利用者登録画面で地名を2文字以上入力すると候補が表示され、選択した場所のIANAタイムゾーンが自動入力されます。地名検索には [Photon](https://github.com/komoot/photon)（OpenStreetMapデータ）、座標からのタイムゾーン解決には [Open-Meteo](https://open-meteo.com/en/docs) を使用するため、インターネット接続が必要です。検索できない場合は、従来どおりIANA名を直接入力できます。本番利用前に、各APIの利用条件と必要な契約・APIキー、またはセルフホストへの切り替えを確認してください。

### `No time zone found with key Asia/Tokyo` が出る場合

古いZIPから作った仮想環境では `tzdata` がまだ入っていない可能性があります。
仮想環境を有効化して次を実行してください。

```bash
python -m pip install -r requirements.txt
```

それでも同じ場合は、直接次を実行します。

```bash
python -m pip install --upgrade tzdata
```

## 最短の試し方

1. アカウントでログイン
2. 授業一覧から 該当箇所 を開く
3. 管理者が日本時間で候補日時を提示し、講師と生徒がそれぞれ候補に `○`
4. または、講師・生徒がタイムゾーンと現地時刻を選んで候補日時を提示
5. 必要な相手が承諾すると、その候補日時で自動確定
6. 共有メッセージをコピーしてLINEやメールへ貼り付け
7. 任意で担当講師がZoom参加リンク・ミーティングID・パスワードを入力

## データ
ローカル標準構成ではSQLiteの `scheduler.db` を同ディレクトリに作成します。
Render構成ではRender Postgres、AWS向けCloudFormation構成ではRDS PostgreSQLへ保存します。
コンテナイメージを更新してWeb ServiceやEC2インスタンスが交換されても、データは
PostgreSQLに保持されます。

