# Google Ads MCP Server

**AIと会話するだけで、Google広告の管理・分析ができるMCPサーバー**

<p align="center">
  <img src="google-ads.svg" alt="Google Ads MCP" width="120">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

Google広告の管理画面でやっている操作 ― レポートの確認、キーワードの追加、広告文の編集、予算変更、PMAX運用まで ― を、AI（Claude / Cursor）とのチャットだけで完結できます。

---

## 特徴

- **30以上のツール** を搭載。読み取り・書き込みの両方に対応
- **安全設計**: 書き込み操作はデフォルトでドライラン（プレビューのみ）
- **GAQL対応**: Google Ads Query Language で自由にデータ取得可能
- **OAuth / サービスアカウント** の両方に対応
- **Claude Desktop / Cursor** どちらからでも利用可能

---

## できること

### 読み取り系（見る・調べる）

| 機能 | 説明 |
|---|---|
| アカウント一覧 | アクセス可能なGoogle広告アカウントを一覧表示 |
| キャンペーン成果 | クリック数・表示回数・費用・CVなど、期間指定で確認 |
| 広告パフォーマンス | 広告ごとの成果を比較 |
| 広告クリエイティブ確認 | 配信中の広告文（見出し・説明文・URL）を確認 |
| 画像アセット | 画像素材の一覧表示・ダウンロード・分析 |
| PMAXアセットグループ | Performance Maxのアセットグループをステータス付きで一覧 |
| GAQL自由クエリ | Google Ads Query Languageで任意のデータを取得 |

### 書き込み系（変える・操作する）

| カテゴリ | 機能 |
|---|---|
| **キャンペーン** | 一時停止/再開、日予算変更、入札戦略変更（目標CPA・目標ROAS・クリック最大化等） |
| **検索広告** | キーワード追加/削除、除外キーワード追加（共有リスト/キャンペーン）、広告グループ停止/再開、広告停止/再開、RSA編集/新規作成 |
| **PMAX** | アセットグループ停止/再開、テキストアセット追加、画像・動画リンク、アセット削除 |
| **広告表示オプション** | サイトリンク追加、コールアウト追加、構造化スニペット追加 |

> 書き込み系はすべてデフォルトで **ドライラン（プレビューのみ）** です。内容を確認してから `dry_run=false` で本番実行します。

---

## クイックスタート

### 前提条件

- **Python 3.11以上** ([ダウンロード](https://www.python.org/downloads/))
- **Google広告 MCC（マネージャーアカウント）** （[作成はこちら](https://ads.google.com/intl/ja_jp/home/tools/manager-accounts/)）
- **Claude Desktop** または **Cursor**

### Step 1: リポジトリをクローン

```bash
git clone https://github.com/nishi0077/google-ads-mcp.git
cd google-ads-mcp
```

### Step 2: 環境構築

```bash
# 仮想環境を作成
python -m venv .venv

# 有効化
# Mac/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# パッケージをインストール
pip install -r requirements.txt
```

### Step 3: Google Ads APIの認証情報を取得

#### 3-1. デベロッパートークンの取得

1. [Google広告](https://ads.google.com) に **MCCアカウント** でログイン
2. ツールと設定（スパナアイコン） → 「APIセンター」
3. 利用規約に同意し、デベロッパートークンを取得

> **重要**: 申請直後は「テストアカウントアクセス」です。本番アカウントを操作するには **Basic Access** への昇格申請が必要です（通常数営業日で承認）。

<details>
<summary>Basic Accessの申請手順（クリックで展開）</summary>

1. MCC → ツールと設定 → APIセンター
2. 「Basic Access を申請」をクリック
3. 利用目的を記入（例: 自社Google広告アカウントの管理・分析）
4. 送信後、Googleの審査を経て承認

自社アカウントの管理目的であれば、比較的通りやすいです。

</details>

#### 3-2. OAuth認証の設定

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. 「APIとサービス」で **Google Ads API** を有効化
3. 「OAuth同意画面」を設定（ユーザータイプ: 外部）
4. **テストユーザーに自分のGoogleアカウントを追加**
5. 「認証情報」→「OAuthクライアントID」を作成（種類: デスクトップアプリ）
6. JSONファイルをダウンロード

### Step 4: 環境変数を設定

```bash
cp .env.example .env
```

`.env` を編集:

```env
GOOGLE_ADS_AUTH_TYPE=oauth
GOOGLE_ADS_CREDENTIALS_PATH=/path/to/your/client_secret.json
GOOGLE_ADS_DEVELOPER_TOKEN=your_developer_token_here
GOOGLE_ADS_LOGIN_CUSTOMER_ID=your_mcc_id_here
```

### Step 5: 初回OAuth認証

```bash
python google_ads_server.py
```

ブラウザが開き、Googleログイン画面が表示されます。許可すると `google_ads_token.json` にトークンが保存され、以降は自動更新されます。

### Step 6: AIツールに接続

#### Claude Desktopの場合

設定ファイルに追加:

**Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/path/to/google-ads-mcp/.venv/bin/python",
      "args": ["/path/to/google-ads-mcp/google_ads_server.py"]
    }
  }
}
```

> Windows の場合は `python` パスを `.venv\\Scripts\\python.exe` に変更

#### Cursorの場合

`.cursor/mcp.json` に追加:

```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/path/to/google-ads-mcp/.venv/bin/python",
      "args": ["/path/to/google-ads-mcp/google_ads_server.py"]
    }
  }
}
```

設定後、アプリを再起動すれば準備完了です。

---

## 使い方

AIに話しかけるだけで使えます:

```
「アカウント一覧を見せて」

「過去30日間のキャンペーン成果を教えて」

「キャンペーンの日予算を20,000円に変更して」

「このキャンペーンを一時停止して」

「入札戦略を目標CPA 3,000円に変更して」

「広告グループにキーワード "不用品回収 即日" をフレーズ一致で追加して」

「除外キーワードに "自分で" "DIY" を追加して」

「PMAXのアセットグループ一覧を見せて」

「サイトリンクを追加して：料金表 → https://example.com/price」
```

---

## トラブルシューティング

| 症状 | 対処法 |
|---|---|
| Pythonが見つからない | `python --version` で 3.11+ か確認 |
| 接続エラー | `.env` のパス・トークンを確認。アプリを再起動 |
| API制限エラー | Google Cloud Console で APIクォータを確認 |
| トークン期限切れ | `google_ads_token.json` を削除して再認証 |
| APIバージョンエラー | `.env` に `GOOGLE_ADS_API_VERSION=v23` を追加 |
| 本番に接続できない | デベロッパートークンのBasic Access昇格が必要 |
| OAuth 403エラー | OAuth同意画面でテストユーザーに自分を追加 |

---

## APIバージョン

Google Ads APIは約3ヶ月ごとに新バージョンがリリースされます。

- **現在のデフォルト**: `v22`
- **リリースノート**: [Google Ads API Release Notes](https://developers.google.com/google-ads/api/docs/release-notes)

バージョンエラーが出た場合は `.env` の `GOOGLE_ADS_API_VERSION` を最新に変更してください。

---

## ライセンス

[MIT License](LICENSE) - 自由にご利用いただけます。

---

## コントリビュート

Issue や Pull Request を歓迎します。バグ報告や機能リクエストは [Issues](https://github.com/nishi0077/google-ads-mcp/issues) からお願いします。
