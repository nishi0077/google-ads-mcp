# 【無料公開】AIと会話するだけでGoogle広告を運用できるMCPサーバーを作った

## はじめに

Google広告を運用していると、管理画面での作業が地味に面倒だと感じたことはありませんか？

- レポートの確認のために毎回管理画面を開く
- キーワードの追加・除外のために何画面も遷移する
- 予算変更のたびにキャンペーン設定を探す
- 複数アカウントの切り替えが煩雑

こうした日常の運用作業を、**AIに話しかけるだけで完結**させるツールを作りました。

**Google Ads MCP Server** は、Claude や Cursor などのAIツールから Google広告の管理・分析をすべてチャットで行えるようにする MCP（Model Context Protocol）サーバーです。

GitHub: https://github.com/nishi0077/google-ads-mcp

---

## MCP（Model Context Protocol）とは？

MCP は、AIモデルが外部のツールやデータソースとやり取りするための標準プロトコルです。Anthropic が提唱し、現在は多くのAIツールでサポートされています。

簡単に言うと、**AIが外部のAPIやサービスを「ツール」として呼び出せるようにする仕組み**です。

```
ユーザー → AI（Claude / Cursor） → MCPサーバー → Google Ads API
         ← 分析・提案            ← データ取得     ← 広告データ
```

MCP サーバーを起動しておけば、AIが必要に応じて自動的にGoogle Ads APIを呼び出し、データの取得や操作を行ってくれます。

---

## このツールでできること

### 読み取り系（34ツール搭載）

AIに「○○を見せて」と言うだけで、以下のデータを取得できます。

- **アカウント一覧**: 管理しているGoogle広告アカウントの一覧
- **キャンペーン成果**: クリック数、表示回数、費用、コンバージョン等を期間指定で取得
- **広告パフォーマンス**: 広告ごとの成果比較
- **広告クリエイティブ**: 配信中の見出し・説明文・URLの一覧
- **画像アセット**: 画像素材の一覧・ダウンロード・パフォーマンス分析
- **PMAXアセットグループ**: Performance Max のアセットグループ一覧
- **GAQL自由クエリ**: Google Ads Query Language で任意のデータ取得

### 書き込み系

「○○を変更して」と指示するだけで、以下の操作ができます。

**キャンペーン管理:**
- キャンペーンの一時停止・再開
- 日予算の変更
- 入札戦略の変更（目標CPA、目標ROAS、クリック最大化など）

**検索広告:**
- キーワードの追加・削除
- 除外キーワードの追加（共有リスト / キャンペーン単位）
- 広告グループの一時停止・再開
- レスポンシブ検索広告の編集・新規作成

**Performance Max:**
- アセットグループの停止・再開
- テキストアセット（見出し・説明文）の追加
- 画像・動画アセットの紐付け・削除

**広告表示オプション:**
- サイトリンク、コールアウト、構造化スニペットの追加

### 安全設計

**すべての書き込み操作はデフォルトでドライラン（プレビューモード）** です。

```
ユーザー: 「このキャンペーンの日予算を30,000円に変更して」

AI: 変更内容をプレビューします：
    キャンペーン: 不用品回収_検索
    現在の日予算: ¥20,000
    変更後の日予算: ¥30,000
    
    この変更を実行しますか？（dry_run=false で本番実行）
```

「OK、実行して」と言って初めて本番に反映されます。誤操作の心配がありません。

---

## 実際の使い方

### 例1: レポートの確認

```
「過去7日間のキャンペーン成果を教えて」
```

AIがGoogle Ads APIからデータを取得し、わかりやすく整形して返してくれます。費用対効果が悪いキャンペーンの指摘や改善提案まで自動で行います。

### 例2: キーワードの追加

```
「広告グループ"不用品回収_一般"に "不用品回収 即日対応" をフレーズ一致で追加して」
```

管理画面を開かずに、チャットだけでキーワード追加が完了します。

### 例3: 除外キーワードの一括追加

```
「除外キーワードに "求人" "バイト" "仕事" を追加して」
```

複数の除外キーワードもまとめて追加できます。

### 例4: 入札戦略の変更

```
「このキャンペーンの入札戦略を目標CPA 5,000円に変更して」
```

### 例5: GAQLで自由にデータ取得

```
「過去30日間でCPAが10,000円を超えているキーワードを一覧にして」
```

AIが適切なGAQLクエリを自動生成し、条件に合うデータを取得してくれます。

---

## セットアップ手順

### 必要なもの

1. **Python 3.11以上**
2. **Google広告 MCCアカウント**（マネージャーアカウント）
3. **Claude Desktop** または **Cursor**

### 1. リポジトリをクローン

```bash
git clone https://github.com/nishi0077/google-ads-mcp.git
cd google-ads-mcp
```

### 2. 環境構築

```bash
# 仮想環境を作成して有効化
python -m venv .venv

# Mac/Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

# 依存パッケージをインストール
pip install -r requirements.txt
```

### 3. Google Ads API の認証情報を取得

この手順が一番大変ですが、一度設定すれば以降は不要です。

#### デベロッパートークンの取得

1. [Google広告](https://ads.google.com) に **MCCアカウント** でログイン
2. ツールと設定（スパナアイコン）→「APIセンター」
3. 利用規約に同意してトークンを取得

**注意:** 申請直後は「テストアカウントアクセス」の状態です。本番アカウントを操作するには **Basic Access** への昇格申請が必要です（自社運用目的なら通常数日で承認されます）。

#### OAuth 認証の設定

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. 「APIとサービス」で **Google Ads API** を有効化
3. 「OAuth同意画面」を設定
   - ユーザータイプ: **外部**
   - テストユーザーに **自分のGoogleアカウントを追加**（これが重要）
4. 「認証情報」→「OAuthクライアントID」を作成
   - アプリケーションの種類: **デスクトップアプリケーション**
5. JSONファイルをダウンロード

### 4. 環境変数の設定

```bash
cp .env.example .env
```

`.env` ファイルを編集:

```env
GOOGLE_ADS_AUTH_TYPE=oauth
GOOGLE_ADS_CREDENTIALS_PATH=/path/to/your/client_secret.json
GOOGLE_ADS_DEVELOPER_TOKEN=your_developer_token_here
GOOGLE_ADS_LOGIN_CUSTOMER_ID=1234567890
```

| 項目 | 説明 |
|---|---|
| `GOOGLE_ADS_AUTH_TYPE` | `oauth`（個人利用）または `service_account`（組織利用） |
| `GOOGLE_ADS_CREDENTIALS_PATH` | ダウンロードした OAuth JSON ファイルのパス |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | MCCで取得したデベロッパートークン |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCCのアカウントID（ハイフンなし10桁） |

### 5. 初回OAuth認証

```bash
python google_ads_server.py
```

ブラウザが自動で開き、Googleログイン画面が表示されます。許可すると `google_ads_token.json` にトークンが保存され、以降は自動更新されるのでこの作業は一度だけです。

### 6. AIツールに接続

#### Claude Desktop

`claude_desktop_config.json` に追加:

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

**設定ファイルの場所:**
- Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

#### Cursor

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

> `/path/to/` は実際のパスに置き換えてください。Windows は `python` を `.venv\\Scripts\\python.exe` に変更。

設定後、アプリを再起動すれば完了です。

---

## 技術的な構成

### アーキテクチャ

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│                  │     │                  │     │                  │
│  Claude/Cursor   │────▶│  MCP Server      │────▶│  Google Ads API  │
│  (AIクライアント)  │ MCP │ (google_ads_     │REST │  (REST API v22)  │
│                  │◀────│  server.py)      │◀────│                  │
│                  │     │                  │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

### 使用技術

| 技術 | 用途 |
|---|---|
| **Python 3.11+** | サーバー実装 |
| **FastMCP** | MCPサーバーフレームワーク |
| **Google Ads API (REST)** | 広告データの取得・操作 |
| **OAuth 2.0** | 認証 |
| **GAQL** | Google Ads Query Language によるデータ取得 |

### MCPツール一覧（34ツール）

**読み取り系:**
`list_accounts`, `get_campaign_performance`, `get_ad_performance`, `get_ad_creatives`, `get_account_currency`, `get_image_assets`, `download_image_asset`, `get_asset_usage`, `analyze_image_assets`, `list_resources`, `list_asset_groups`, `execute_gaql_query`, `run_gaql`

**書き込み系:**
`pause_enable_campaign`, `update_campaign_budget`, `update_campaign_bidding`, `add_keywords`, `remove_keyword`, `update_keyword_bids`, `add_negative_keywords`, `remove_negative_keywords`, `add_campaign_negative_keywords`, `pause_enable_ad_group`, `update_ad_status`, `edit_responsive_search_ad`, `create_responsive_search_ad`, `pause_enable_asset_group`, `add_asset_group_text_assets`, `link_asset_to_asset_group`, `remove_asset_group_asset`, `add_sitelink_extensions`, `add_callout_extensions`, `add_structured_snippets`

---

## なぜ作ったのか

広告運用の現場では、データの確認や設定変更のために管理画面を何度も行き来する必要があります。特に複数アカウントを運用している場合、この作業時間は馬鹿になりません。

MCPの登場により「AIが外部APIを直接呼び出す」ことが標準化されたので、**広告運用の定型作業をAIに任せられる**仕組みを作りました。

使ってみると分かりますが、「過去1週間の成果を見せて」→「CPAが高いキーワードを除外して」→「予算を調整して」という一連の流れがチャットだけで完結するのは、思った以上に快適です。

---

## まとめ

- **Google Ads MCP Server** は、AIチャットからGoogle広告を管理・分析できるMCPサーバー
- 34ツール搭載、読み取り・書き込みの両方に対応
- ドライランモードで安全に操作可能
- MIT ライセンスで無料公開中

興味がある方はぜひ試してみてください。

**GitHub**: https://github.com/nishi0077/google-ads-mcp

Issue や Pull Request も歓迎です。

---

*この記事で紹介したツールは Google 公式のものではありません。Google Ads API を利用する際は、Googleの利用規約を遵守してください。*
