import XCTest
@testable import KSSDesktop

final class ScheduleStructTests: XCTestCase {
    func testDailyWithWeekdaysSerializesHourMinuteWeekdays() throws {
        let s = ScheduleStruct(hour: 18, minute: 30, weekdays: [1, 2, 3, 4, 5], weekly: false, weekday: nil)
        let json = s.toScheduleJSON()
        let data = try XCTUnwrap(json.data(using: .utf8))
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(obj?["hour"] as? Int, 18)
        XCTAssertEqual(obj?["minute"] as? Int, 30)
        XCTAssertEqual(obj?["weekdays"] as? [Int], [1, 2, 3, 4, 5])
        XCTAssertNil(obj?["weekly"])
    }

    func testDailyWithoutWeekdaysOmitsWeekdaysKey() throws {
        let s = ScheduleStruct(hour: 9, minute: 0, weekdays: nil, weekly: false, weekday: nil)
        let json = s.toScheduleJSON()
        let data = try XCTUnwrap(json.data(using: .utf8))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(obj["hour"] as? Int, 9)
        XCTAssertNil(obj["weekdays"])
    }

    func testWeeklySerializesNestedWeeklyObject() throws {
        let s = ScheduleStruct(hour: 20, minute: 0, weekdays: nil, weekly: true, weekday: 5)
        let json = s.toScheduleJSON()
        let data = try XCTUnwrap(json.data(using: .utf8))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let weekly = try XCTUnwrap(obj["weekly"] as? [String: Any])
        XCTAssertEqual(weekly["weekday"] as? Int, 5)
        XCTAssertEqual(weekly["hour"] as? Int, 20)
        XCTAssertEqual(weekly["minute"] as? Int, 0)
        XCTAssertNil(obj["hour"])
    }

    func testWeeklyDefaultsWeekdayToOneWhenNil() throws {
        let s = ScheduleStruct(hour: 20, minute: 0, weekdays: nil, weekly: true, weekday: nil)
        let json = s.toScheduleJSON()
        let data = try XCTUnwrap(json.data(using: .utf8))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let weekly = try XCTUnwrap(obj["weekly"] as? [String: Any])
        XCTAssertEqual(weekly["weekday"] as? Int, 1)
    }
}
