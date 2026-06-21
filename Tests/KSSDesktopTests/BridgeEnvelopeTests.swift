import XCTest
@testable import KSSDesktop

/// U4 版本化桥协议（KTD3）：`BridgeClient.decodeEnvelope` 的两段解码 +
/// 版本不匹配出 `.schemaMismatch`、缺字段视为 v0、data 正常解包。
/// 注：本机 CLT 无 XCTest，需完整 Xcode `swift test`。
final class BridgeEnvelopeTests: XCTestCase {

    private struct Payload: Decodable, Equatable { let n: Int; let s: String }

    private func data(_ json: String) -> Data { Data(json.utf8) }

    func testMatchedVersionUnwrapsData() throws {
        let d = data(#"{"schemaVersion":1,"data":{"n":42,"s":"ok"}}"#)
        let p: Payload = try BridgeClient.decodeEnvelope(d)
        XCTAssertEqual(p, Payload(n: 42, s: "ok"))
    }

    func testBridgeAheadThrowsSchemaMismatch() {
        let d = data(#"{"schemaVersion":2,"data":{"n":1,"s":"x"}}"#)
        XCTAssertThrowsError(try BridgeClient.decodeEnvelope(d) as Payload) { err in
            guard case BridgeError.schemaMismatch(let bridge, let app) = err else {
                return XCTFail("期望 schemaMismatch，得到 \(err)")
            }
            XCTAssertEqual(bridge, 2); XCTAssertEqual(app, 1)
        }
    }

    func testMissingSchemaVersionTreatedAsV0Mismatch() {
        // 旧/未包裹输出（无 schemaVersion）→ v0 → 不匹配，出横幅而非静默崩。
        let d = data(#"{"n":1,"s":"x"}"#)
        XCTAssertThrowsError(try BridgeClient.decodeEnvelope(d) as Payload) { err in
            guard case BridgeError.schemaMismatch(let bridge, _) = err else {
                return XCTFail("期望 schemaMismatch，得到 \(err)")
            }
            XCTAssertEqual(bridge, 0)
        }
    }

    func testMatchedVersionButBadDataThrowsInvalidOutput() {
        // 版本对、data 形态错 → invalidOutput（非 schemaMismatch）。
        let d = data(#"{"schemaVersion":1,"data":{"n":"not-an-int"}}"#)
        XCTAssertThrowsError(try BridgeClient.decodeEnvelope(d) as Payload) { err in
            guard case BridgeError.invalidOutput = err else {
                return XCTFail("期望 invalidOutput，得到 \(err)")
            }
        }
    }

    func testSchemaMismatchMessageDirectsCorrectly() {
        let tooNew = BridgeError.schemaMismatch(bridge: 2, app: 1).errorDescription ?? ""
        XCTAssertTrue(tooNew.contains("重新编译"), "脚本过新应提示重编 App")
        let tooOld = BridgeError.schemaMismatch(bridge: 0, app: 1).errorDescription ?? ""
        XCTAssertTrue(tooOld.contains("git pull"), "脚本过旧应提示更新脚本")
    }
}
