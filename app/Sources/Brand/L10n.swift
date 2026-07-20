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
    var lang: AppLang = .system {
        didSet { UserDefaults.standard.set(lang.rawValue, forKey: "rekey.language") }
    }
    var systemCode: String = "en"

    init() {
        if let s = UserDefaults.standard.string(forKey: "rekey.language"),
           let a = AppLang(rawValue: s) { lang = a }
    }

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

    /// True when chrome text should render in Be Vietnam Pro (brand VN body).
    var isVietnamese: Bool { active == "vi" }

    /// Language-aware chrome font (Be Vietnam Pro for vi, Geist Sans otherwise).
    /// Reads `active`, so a view body that calls this re-renders on language
    /// change and picks up the right face.
    func sans(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        Typeface.sans(size, weight, vietnamese: isVietnamese)
    }

    // vi = natural Vietnamese, en, zh = Simplified, ja.
    static let table: [String: [String: String]] = [
        "lang_system":   ["vi": "tự động", "en": "system", "zh": "跟随系统", "ja": "システム"],
        "language":      ["vi": "ngôn ngữ", "en": "language", "zh": "语言", "ja": "言語"],
        "light_dark":    ["vi": "sáng / tối", "en": "light / dark", "zh": "浅色 / 深色", "ja": "ライト / ダーク"],
        "inspector":     ["vi": "chi tiết", "en": "inspector", "zh": "详情", "ja": "詳細"],
        "read":          ["vi": "đọc", "en": "read", "zh": "读取", "ja": "読み取り"],
        "decode":        ["vi": "giải mã", "en": "decode", "zh": "解码", "ja": "デコード"],
        "clone":         ["vi": "nhân bản", "en": "clone", "zh": "克隆", "ja": "複製"],
        "write":         ["vi": "ghi thẻ", "en": "write", "zh": "写入", "ja": "書き込み"],
        "format":        ["vi": "format", "en": "format", "zh": "格式化", "ja": "初期化"],
        "format_q":      ["vi": "format thẻ này về factory?", "en": "format this card to factory?", "zh": "将此卡格式化为出厂状态？", "ja": "このカードを工場出荷状態に初期化しますか？"],
        "format_msg":    ["vi": "xóa toàn bộ data và đặt khóa về ff. không hoàn tác được. block 0 (uid) giữ nguyên.", "en": "erases all data and resets keys to ff. cannot be undone. block 0 (uid) is left intact.", "zh": "清除所有数据并将密钥重置为 ff，不可撤销。第 0 块（uid）保持不变。", "ja": "全データを消去し鍵を ff に戻します。元に戻せません。ブロック0（uid）はそのまま。"],
        "clone_q":       ["vi": "ghi đè thẻ trên đầu đọc?", "en": "overwrite the card on the reader?", "zh": "覆盖读卡器上的卡片？", "ja": "リーダー上のカードを上書きしますか？"],
        "clone_msg":     ["vi": "ghi data lên thẻ đích, không hoàn tác được. khi bật ghi khóa/uid, access-bits hỏng có thể làm hỏng vĩnh viễn một sector.", "en": "writes the source data onto the target card and cannot be undone. with keys/uid enabled, bad access bits can permanently lock a sector.", "zh": "将源数据写入目标卡，不可撤销。启用密钥/uid 写入时，错误的访问位可能永久锁定扇区。", "ja": "ソースのデータを対象カードに書き込みます。元に戻せません。鍵/uid 書き込みが有効な場合、不正なアクセスビットでセクタが永久にロックされることがあります。"],
        "recover":       ["vi": "khôi phục khóa", "en": "recover keys", "zh": "恢复密钥", "ja": "鍵を復元"],
        "apdu":          ["vi": "apdu", "en": "apdu", "zh": "apdu", "ja": "apdu"],
        "soon":          ["vi": "sắp có", "en": "soon", "zh": "即将推出", "ja": "近日対応"],
        "decode_card":   ["vi": "giải mã thẻ", "en": "decode card", "zh": "解码卡片", "ja": "カードをデコード"],
        "read_card":     ["vi": "đọc thẻ", "en": "read card", "zh": "读取卡片", "ja": "カードを読み取る"],
        "read_all":      ["vi": "đọc toàn bộ sector + khóa", "en": "read all sectors + keys", "zh": "读取所有扇区与密钥", "ja": "全セクターと鍵を読み取る"],
        "read_pages":    ["vi": "đọc toàn bộ page", "en": "read all pages", "zh": "读取所有页", "ja": "全ページを読み取る"],
        "page":          ["vi": "page", "en": "page", "zh": "页", "ja": "ページ"],
        "bytes":         ["vi": "byte", "en": "bytes", "zh": "字节", "ja": "バイト"],
        "decoding":      ["vi": "đang giải mã…", "en": "decoding…", "zh": "解码中…", "ja": "デコード中…"],
        "trying_keys":   ["vi": "thử khóa", "en": "trying keys", "zh": "尝试密钥", "ja": "鍵を試行"],
        "no_keys_title": ["vi": "không tìm thấy khóa", "en": "no keys found", "zh": "未找到密钥", "ja": "鍵が見つかりません"],
        "no_keys_msg":   ["vi": "không tìm được khóa nào trong từ điển cho thẻ này. nếu bạn có khóa, thêm ở cài đặt (từ điển khóa) rồi giải mã lại. nếu không có khóa thì bản này chưa đọc được thẻ - chưa hỗ trợ dò khóa cho thẻ hoàn toàn lạ.", "en": "no key in the dictionary matched this card. if you have a key, add it in settings (key dictionary) and decode again. without a key, this build cannot read the card yet - key recovery for a fully unknown card is not available.", "zh": "字典中没有匹配此卡的密钥。如果您有密钥，请在设置（密钥字典）中添加后重新解码。若没有密钥，此版本暂时无法读取该卡 - 尚不支持对完全未知卡的密钥恢复。", "ja": "辞書内にこのカードと一致する鍵がありませんでした。鍵をお持ちなら設定（鍵辞書）で追加して再度デコードしてください。鍵がない場合、このビルドではまだ読み取れません - 全く未知のカードの鍵復元は未対応です。"],
        "not_decoded":   ["vi": "chưa giải", "en": "not decoded", "zh": "未解码", "ja": "未デコード"],
        "waiting_card":  ["vi": "đang chờ thẻ", "en": "waiting for card", "zh": "等待卡片", "ja": "カードを待機中"],
        "reader_offline":["vi": "chưa có đầu đọc", "en": "reader offline", "zh": "读卡器离线", "ja": "リーダー オフライン"],
        "reader_online": ["vi": "đầu đọc sẵn sàng", "en": "reader online", "zh": "读卡器在线", "ja": "リーダー オンライン"],
        "card":          ["vi": "thẻ", "en": "card", "zh": "卡片", "ja": "カード"],
        "document":      ["vi": "tài liệu", "en": "document", "zh": "文档", "ja": "ドキュメント"],
        "place_target":  ["vi": "đặt thẻ đích lên đầu đọc rồi bấm ghi thẻ", "en": "place the target card on the reader, then press write", "zh": "将目标卡放到读卡器上，然后点击写入", "ja": "対象カードをリーダーに置き、書き込みを押す"],
        "decode_to_read":["vi": "thẻ đang cắm khác nguồn - bấm giải mã để đọc thẻ này, hoặc ghi để chép nguồn lên nó", "en": "the card on the reader differs from the document - decode to read it, or write to copy the document onto it", "zh": "读卡器上的卡与文档不同 - 解码以读取它，或写入以将文档复制到它上面", "ja": "リーダー上のカードは文書と異なります - デコードで読み取るか、書き込みで文書をコピー"],
        "type":          ["vi": "loại", "en": "type", "zh": "类型", "ja": "種類"],
        "select_sector": ["vi": "chọn một sector", "en": "select a sector", "zh": "选择一个扇区", "ja": "セクターを選択"],
        "sector":        ["vi": "sector", "en": "sector", "zh": "扇区", "ja": "セクター"],
        "sectors":       ["vi": "sector", "en": "sectors", "zh": "扇区", "ja": "セクター"],
        "key":           ["vi": "khóa", "en": "key", "zh": "密钥", "ja": "鍵"],
        "blocks":        ["vi": "block", "en": "blocks", "zh": "数据块", "ja": "ブロック"],
        "block":         ["vi": "block", "en": "block", "zh": "数据块", "ja": "ブロック"],
        "access":        ["vi": "quyền truy cập", "en": "access", "zh": "访问权限", "ja": "アクセス権"],
        "access_invalid":["vi": "bit quyền không hợp lệ", "en": "access bits invalid", "zh": "权限位无效", "ja": "アクセスビット不正"],
        "copy_block":    ["vi": "sao chép block", "en": "copy block", "zh": "复制数据块", "ja": "ブロックをコピー"],
        "role_manufacturer": ["vi": "nhà sản xuất", "en": "manufacturer", "zh": "厂商块", "ja": "メーカー"],
        "role_data":     ["vi": "dữ liệu", "en": "data", "zh": "数据", "ja": "データ"],
        "role_trailer":  ["vi": "trailer (khóa)", "en": "trailer (keys)", "zh": "尾块（密钥）", "ja": "トレーラー（鍵）"],
        "open_dump":     ["vi": "mở dump…", "en": "open dump…", "zh": "打开转储…", "ja": "ダンプを開く…"],
        "save_dump":     ["vi": "lưu dump…", "en": "save dump…", "zh": "保存转储…", "ja": "ダンプを保存…"],
        "source":        ["vi": "nguồn", "en": "source", "zh": "源", "ja": "ソース"],
        "no_source":     ["vi": "chưa có nguồn", "en": "no source dump", "zh": "无源转储", "ja": "ソースなし"],
        "card_on_reader":["vi": "thẻ trên đầu đọc", "en": "card on reader", "zh": "读卡器上的卡", "ja": "リーダー上のカード"],
        "write_trailers":["vi": "ghi cả trailer (khóa / quyền)", "en": "write trailers (keys / access)", "zh": "写入尾块（密钥/权限）", "ja": "トレーラーを書き込む（鍵/アクセス）"],
        "write_uid":     ["vi": "ghi block 0 (uid)", "en": "write block 0 (uid)", "zh": "写入第 0 块（uid）", "ja": "ブロック0を書き込む（uid）"],
        "write_trailers_hint": ["vi": "chép khóa của nguồn sang thẻ đích. tắt = giữ khóa sẵn có của thẻ đích, chỉ ghi dữ liệu.", "en": "copy the source keys onto the target. off = keep the target's own keys, write data only.", "zh": "把源卡的密钥写入目标卡。关闭 = 保留目标卡原有密钥，仅写数据。", "ja": "ソースの鍵を対象カードに書き込む。オフ = 対象カードの鍵を保持しデータのみ書き込む。"],
        "write_uid_hint": ["vi": "đổi cả số uid của thẻ. hầu hết thẻ không cho, chỉ thẻ magic (uid ghi được).", "en": "also overwrite the card uid. most cards refuse this; magic (uid-writable) cards only.", "zh": "同时覆盖卡片 uid。多数卡片不允许，仅限魔术卡（uid 可写）。", "ja": "カードの uid も上書き。多くのカードは拒否、magic（uid 書込可）カードのみ。"],
        "uid_warning":   ["vi": "ghi đè uid - chỉ dùng cho thẻ magic; thẻ thường sẽ hỏng block 0", "en": "overwrites the card uid - magic cards only; a normal card will reject or brick block 0", "zh": "覆盖卡片 uid - 仅限魔术卡；普通卡会损坏第 0 块", "ja": "カードの uid を上書き - magic カード専用；通常のカードはブロック0を破損します"],
        "cancel":        ["vi": "hủy", "en": "cancel", "zh": "取消", "ja": "キャンセル"],
        "write_to_card": ["vi": "ghi vào thẻ", "en": "write to card", "zh": "写入卡片", "ja": "カードに書き込む"],
        "apdu_hint":     ["vi": "nhập apdu hex rồi enter", "en": "type a hex apdu, press return", "zh": "输入十六进制 apdu 后回车", "ja": "16進 apdu を入力し return"],
        "apdu_empty":    ["vi": "chưa có lệnh nào", "en": "no commands yet", "zh": "暂无命令", "ja": "コマンドなし"],
        "apdu_no_response": ["vi": "không phản hồi", "en": "no response", "zh": "无响应", "ja": "応答なし"],
        "apdu_no_card":  ["vi": "chưa có thẻ", "en": "no card", "zh": "无卡片", "ja": "カードなし"],
        "apdu_error":    ["vi": "lỗi", "en": "error", "zh": "错误", "ja": "エラー"],
        "device":        ["vi": "thiết bị", "en": "device", "zh": "设备", "ja": "デバイス"],
        "dictionaries":  ["vi": "từ điển khóa", "en": "dictionaries", "zh": "密钥字典", "ja": "辞書"],
        "general":       ["vi": "chung", "en": "general", "zh": "通用", "ja": "一般"],
        "model":         ["vi": "model", "en": "model", "zh": "型号", "ja": "型番"],
        "serial":        ["vi": "serial", "en": "serial", "zh": "序列号", "ja": "シリアル"],
        "status":        ["vi": "trạng thái", "en": "status", "zh": "状态", "ja": "状態"],
        "reconnect":     ["vi": "kết nối lại", "en": "reconnect", "zh": "重新连接", "ja": "再接続"],
        "key_hint":      ["vi": "12 ký tự hex", "en": "12 hex chars", "zh": "12 位十六进制", "ja": "16進12文字"],
        "add":           ["vi": "thêm", "en": "add", "zh": "添加", "ja": "追加"],
        "import":        ["vi": "nhập tệp…", "en": "import…", "zh": "导入…", "ja": "インポート…"],
        "remove":        ["vi": "xóa", "en": "remove", "zh": "删除", "ja": "削除"],
        "keys_count":    ["vi": "khóa", "en": "keys", "zh": "个密钥", "ja": "件の鍵"],
        "user_keys":     ["vi": "khóa của bạn", "en": "user keys", "zh": "用户密钥", "ja": "ユーザー鍵"],
        "builtin_keys":  ["vi": "khóa tích hợp", "en": "built-in", "zh": "内置", "ja": "内蔵"],
        "learned_keys":  ["vi": "khóa đã học", "en": "learned", "zh": "已学", "ja": "学習済み"],
        "clear_learned": ["vi": "xóa đã học", "en": "clear learned", "zh": "清除已学", "ja": "学習を消去"],
        "appearance":    ["vi": "giao diện", "en": "appearance", "zh": "外观", "ja": "外観"],
        "light":         ["vi": "sáng", "en": "light", "zh": "浅色", "ja": "ライト"],
        "dark":          ["vi": "tối", "en": "dark", "zh": "深色", "ja": "ダーク"],
        "export_folder": ["vi": "thư mục lưu", "en": "export folder", "zh": "导出文件夹", "ja": "保存先フォルダ"],
        "export_default":["vi": "hỏi mỗi lần", "en": "ask each time", "zh": "每次询问", "ja": "毎回確認"],
        "choose":        ["vi": "chọn…", "en": "choose…", "zh": "选择…", "ja": "選択…"],
        "copy_sector":   ["vi": "sao chép sector", "en": "copy sector", "zh": "复制扇区", "ja": "セクターをコピー"],
        "copy_key":      ["vi": "sao chép khóa", "en": "copy key", "zh": "复制密钥", "ja": "鍵をコピー"],
        "prov_nondefault": ["vi": "khóa riêng", "en": "non-default", "zh": "非默认", "ja": "非標準"],
        "prov_dictionary": ["vi": "từ điển", "en": "dictionary", "zh": "字典", "ja": "辞書"],
        "prov_nested":     ["vi": "bẻ nested", "en": "nested-cracked", "zh": "嵌套破解", "ja": "ネスト解読"],
        "prov_unknown":    ["vi": "chưa biết", "en": "unknown", "zh": "未知", "ja": "不明"],
        "read_anyway":     ["vi": "cứ đọc thử", "en": "read anyway", "zh": "仍然读取", "ja": "それでも読み取る"],
        "looking_card":    ["vi": "đang tìm thẻ…", "en": "looking for card…", "zh": "正在寻找卡片…", "ja": "カードを探しています…"],
        "writing":         ["vi": "đang ghi…", "en": "writing…", "zh": "写入中…", "ja": "書き込み中…"],
        "clone_done":      ["vi": "đã ghi xong", "en": "clone complete", "zh": "克隆完成", "ja": "複製完了"],
        "place_next":      ["vi": "đặt thẻ trắng tiếp theo rồi ghi lần nữa", "en": "place the next blank, then write again", "zh": "放入下一张空白卡，然后再次写入", "ja": "次の空白カードを置いて、もう一度書き込む"],
        "write_another":   ["vi": "ghi thẻ khác", "en": "write another", "zh": "写入下一张", "ja": "続けて書き込む"],
        "done":            ["vi": "xong", "en": "done", "zh": "完成", "ja": "完了"],
        "assumed":         ["vi": "suy đoán", "en": "assumed", "zh": "推测", "ja": "推定"],
        "assumed_warning": ["vi": "một số khóa được suy đoán (chép từ khóa còn lại), không phải đọc được. nếu đầu đọc thật xác thực bằng khóa đó, bản sao có thể bị từ chối.", "en": "some keys are assumed (copied from the other slot), not read. if the real reader authenticates with that key, the clone may be rejected.", "zh": "部分密钥是推测得出的（从另一槽复制），并非读取所得。若真实读卡器用该密钥验证，克隆卡可能被拒。", "ja": "一部の鍵は読み取りではなく推定（もう一方から複製）です。実機がその鍵で認証する場合、複製は拒否される可能性があります。"],
        // ---- Chameleon slot library + emulation (P3) ----
        "slots":           ["vi": "khe thẻ", "en": "slots", "zh": "卡槽", "ja": "スロット"],
        "slot":            ["vi": "khe", "en": "slot", "zh": "卡槽", "ja": "スロット"],
        "slot_library":    ["vi": "thư viện khe thẻ", "en": "slot library", "zh": "卡槽库", "ja": "スロット一覧"],
        "active":          ["vi": "đang dùng", "en": "active", "zh": "使用中", "ja": "使用中"],
        "make_active":     ["vi": "chọn làm khe chính", "en": "make active", "zh": "设为使用中", "ja": "使用中にする"],
        "enable":          ["vi": "bật", "en": "enable", "zh": "启用", "ja": "有効化"],
        "disable":         ["vi": "tắt", "en": "disable", "zh": "禁用", "ja": "無効化"],
        "set_type":        ["vi": "đổi loại thẻ", "en": "set type", "zh": "设置类型", "ja": "種類を設定"],
        "rename":          ["vi": "đổi tên", "en": "rename", "zh": "重命名", "ja": "名前を変更"],
        "save_slots":      ["vi": "lưu vào bộ nhớ", "en": "save to flash", "zh": "保存到闪存", "ja": "フラッシュに保存"],
        "empty_slot":      ["vi": "khe trống", "en": "empty", "zh": "空", "ja": "空き"],
        "open_content":    ["vi": "mở nội dung hf", "en": "open hf content", "zh": "打开 hf 内容", "ja": "HF 内容を開く"],
        "load_to_slot":    ["vi": "nạp vào khe", "en": "load to slot", "zh": "载入卡槽", "ja": "スロットに読み込む"],
        "slot_name":       ["vi": "tên khe", "en": "slot name", "zh": "卡槽名称", "ja": "スロット名"],
        "emulate":         ["vi": "giả lập", "en": "emulate", "zh": "模拟", "ja": "エミュレート"],
        "reader_mode":     ["vi": "chế độ đọc", "en": "reader mode", "zh": "读卡模式", "ja": "リーダーモード"],
        "emulate_mode":    ["vi": "chế độ giả lập", "en": "emulate mode", "zh": "模拟模式", "ja": "エミュレートモード"],
        "emulate_hint":    ["vi": "chuyển giữa đọc thẻ và giả lập khe đang chọn", "en": "switch between reading cards and emulating the active slot", "zh": "在读卡与模拟当前卡槽之间切换", "ja": "カード読み取りと現在スロットのエミュレートを切り替える"],
    ]
}
