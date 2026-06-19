import SwiftUI
import Observation

/// Lightweight in-app localization (vi / en / zh / ja) with instant runtime
/// switching - the manager is observable, so reading t() in a view body
/// re-renders when the language changes. Technical tokens (uid/atqa/sak, hex,
/// product names) stay verbatim; only readable chrome is translated.
enum AppLang: String, CaseIterable, Identifiable {
    case system, vi, en, zh, ja
    var id: String { rawValue }
    /// Autonym (shown in its own script); `system` label is itself translated.
    var display: String {
        switch self {
        case .system: "system"
        case .vi: "Tiếng Việt"
        case .en: "English"
        case .zh: "中文"
        case .ja: "日本語"
        }
    }
}

@MainActor
@Observable
final class L10n {
    var lang: AppLang = .system
    var systemCode: String = "en"

    var active: String {
        if lang == .system {
            return ["vi", "en", "zh", "ja"].contains(systemCode) ? systemCode : "en"
        }
        return lang.rawValue
    }

    func t(_ key: String) -> String {
        let row = Self.table[key]
        return row?[active] ?? row?["en"] ?? key
    }

    func systemDisplay() -> String { t("lang_system") }

    // vi = natural Vietnamese, en, zh = Simplified, ja.
    static let table: [String: [String: String]] = [
        "lang_system":   ["vi": "tự động", "en": "system", "zh": "跟随系统", "ja": "システム"],
        "language":      ["vi": "ngôn ngữ", "en": "language", "zh": "语言", "ja": "言語"],
        "light_dark":    ["vi": "sáng / tối", "en": "light / dark", "zh": "浅色 / 深色", "ja": "ライト / ダーク"],
        "inspector":     ["vi": "chi tiết", "en": "inspector", "zh": "详情", "ja": "詳細"],
        "read":          ["vi": "đọc", "en": "read", "zh": "读取", "ja": "読み取り"],
        "decode":        ["vi": "giải mã", "en": "decode", "zh": "解码", "ja": "デコード"],
        "clone":         ["vi": "nhân bản", "en": "clone", "zh": "克隆", "ja": "複製"],
        "recover":       ["vi": "khôi phục khóa", "en": "recover keys", "zh": "恢复密钥", "ja": "鍵を復元"],
        "apdu":          ["vi": "apdu", "en": "apdu", "zh": "apdu", "ja": "apdu"],
        "soon":          ["vi": "sắp có", "en": "soon", "zh": "即将推出", "ja": "近日対応"],
        "decode_card":   ["vi": "giải mã thẻ", "en": "decode card", "zh": "解码卡片", "ja": "カードをデコード"],
        "read_all":      ["vi": "đọc toàn bộ sector + khóa", "en": "read all sectors + keys", "zh": "读取所有扇区与密钥", "ja": "全セクターと鍵を読み取る"],
        "decoding":      ["vi": "đang giải mã…", "en": "decoding…", "zh": "解码中…", "ja": "デコード中…"],
        "waiting_card":  ["vi": "đang chờ thẻ", "en": "waiting for card", "zh": "等待卡片", "ja": "カードを待機中"],
        "reader_offline":["vi": "chưa có đầu đọc", "en": "reader offline", "zh": "读卡器离线", "ja": "リーダー オフライン"],
        "reader_online": ["vi": "đầu đọc sẵn sàng", "en": "reader online", "zh": "读卡器在线", "ja": "リーダー オンライン"],
        "card":          ["vi": "thẻ", "en": "card", "zh": "卡片", "ja": "カード"],
        "type":          ["vi": "loại", "en": "type", "zh": "类型", "ja": "種類"],
        "select_sector": ["vi": "chọn một sector", "en": "select a sector", "zh": "选择一个扇区", "ja": "セクターを選択"],
        "sector":        ["vi": "sector", "en": "sector", "zh": "扇区", "ja": "セクター"],
        "sectors":       ["vi": "sector", "en": "sectors", "zh": "扇区", "ja": "セクター"],
        "key":           ["vi": "khóa", "en": "key", "zh": "密钥", "ja": "鍵"],
        "blocks":        ["vi": "block", "en": "blocks", "zh": "数据块", "ja": "ブロック"],
        "open_dump":     ["vi": "mở dump…", "en": "open dump…", "zh": "打开转储…", "ja": "ダンプを開く…"],
        "save_dump":     ["vi": "lưu dump…", "en": "save dump…", "zh": "保存转储…", "ja": "ダンプを保存…"],
        "source":        ["vi": "nguồn", "en": "source", "zh": "源", "ja": "ソース"],
        "no_source":     ["vi": "chưa có nguồn", "en": "no source dump", "zh": "无源转储", "ja": "ソースなし"],
        "card_on_reader":["vi": "thẻ trên đầu đọc", "en": "card on reader", "zh": "读卡器上的卡", "ja": "リーダー上のカード"],
        "write_trailers":["vi": "ghi cả trailer (khóa / quyền)", "en": "write trailers (keys / access)", "zh": "写入尾块（密钥/权限）", "ja": "トレーラーを書き込む（鍵/アクセス）"],
        "write_uid":     ["vi": "ghi block 0 (uid)", "en": "write block 0 (uid)", "zh": "写入第 0 块（uid）", "ja": "ブロック0を書き込む（uid）"],
        "uid_warning":   ["vi": "ghi đè uid - chỉ dùng cho thẻ magic; thẻ thường sẽ hỏng block 0", "en": "overwrites the card uid - magic cards only; a normal card will reject or brick block 0", "zh": "覆盖卡片 uid - 仅限魔术卡；普通卡会损坏第 0 块", "ja": "カードの uid を上書き - magic カード専用；通常のカードはブロック0を破損します"],
        "cancel":        ["vi": "hủy", "en": "cancel", "zh": "取消", "ja": "キャンセル"],
        "write_to_card": ["vi": "ghi vào thẻ", "en": "write to card", "zh": "写入卡片", "ja": "カードに書き込む"],
        "prov_nondefault": ["vi": "khóa riêng", "en": "non-default", "zh": "非默认", "ja": "非標準"],
        "prov_dictionary": ["vi": "từ điển", "en": "dictionary", "zh": "字典", "ja": "辞書"],
        "prov_nested":     ["vi": "bẻ nested", "en": "nested-cracked", "zh": "嵌套破解", "ja": "ネスト解読"],
        "prov_unknown":    ["vi": "chưa biết", "en": "unknown", "zh": "未知", "ja": "不明"],
    ]
}
