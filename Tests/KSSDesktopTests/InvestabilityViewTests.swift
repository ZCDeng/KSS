import XCTest
@testable import KSSDesktop

/// 可投资地图渲染层的机检门（plan U5/U6/U7）。
///
/// 夹具 JSON 逐字取自 `kss_app_bridge.py investability-map / investability-stocks`
/// 的真实返回：解码契约一漂，四处落点会同时变哑，而那是运行期才看得见的失败。
final class InvestabilityViewTests: XCTestCase {

    // MARK: - 解码契约

    private static let stockJSON = """
    {
      "tsCode": "688017.SH",
      "state": "labelled",
      "stateLabel": "浅绿",
      "colorKey": "light_green",
      "colorLabel": "浅绿",
      "primaryNode": {
        "nodeId": "infotech.04", "name": "半导体设备", "axis": "strategic_industries",
        "group": "infotech", "primaryColor": "light_green", "secondaryColor": "",
        "tier": "second", "reading": "验证突破定倍数", "qualifier": "代理通道偏红",
        "sourceRef": "5.3 新一代信息技术表", "lastReviewed": "2026-08-09", "isPending": false
      },
      "secondaryNodes": [],
      "zone": {"key": "red", "label": "红区", "decided": 8, "yes": 6, "total": 8,
               "display": "红区 · 已定 6/8"},
      "answers": {"1": true, "2": false, "3": null, "4": true, "5": null,
                  "6": true, "7": true, "8": true},
      "labelUpdatedAt": "2026-08-09T10:00:00+08:00",
      "isStale": false
    }
    """

    private func decodeStock() throws -> ExposureStock {
        try JSONDecoder().decode(ExposureStock.self, from: Data(Self.stockJSON.utf8))
    }

    /// 8 问的 null 在 `[String: Bool?]` 上会抛 valueNotFound，自解那段必须真能扛住。
    func testAnswersDecodeTreatsNullAsUndecided() throws {
        let stock = try decodeStock()
        XCTAssertEqual(stock.answers[1], true)
        XCTAssertEqual(stock.answers[2], false)
        XCTAssertNil(stock.answers[3], "null 表示未知/未答，不该落成 false")
        XCTAssertNil(stock.answers[5])
        XCTAssertEqual(stock.answers.decided.count, 6)
    }

    /// 覆盖 AE4：主节点浅绿且判为红区时，色仍是浅绿，区位另算。
    func testRedZoneDoesNotOverwriteIndustryColor() throws {
        let stock = try decodeStock()
        XCTAssertEqual(stock.colorKey, "light_green")
        XCTAssertEqual(stock.primaryNode?.primaryColor, "light_green")
        XCTAssertTrue(stock.zone.isRed)
        XCTAssertEqual(stock.zone.display, "红区 · 已定 6/8", "区位串由桥接算好，渲染层不得改写")
    }

    /// 未上图态：桥接给的是空 colorKey + 未上图文案，不是某个兜底色。
    func testUnlabelledStockDecodesToNeutralState() throws {
        let json = """
        {"tsCode":"600000.SH","state":"unlabelled","stateLabel":"未上图","colorKey":"",
         "colorLabel":"","primaryNode":null,"secondaryNodes":[],
         "zone":{"key":"undetermined","label":"未尽调","decided":0,"yes":0,"total":8,
                 "display":"未尽调 · 已定 0/8"},
         "answers":{"1":null,"2":null,"3":null,"4":null,"5":null,"6":null,"7":null,"8":null},
         "labelUpdatedAt":"","isStale":false}
        """
        let stock = try JSONDecoder().decode(ExposureStock.self, from: Data(json.utf8))
        XCTAssertEqual(stock.state, .unlabelled)
        XCTAssertFalse(stock.isLabelled)
        XCTAssertNil(ThemeCatalog.palette(for: .clayM3, appearance: .dark)
            .exposureColor(forKey: stock.colorKey), "空 colorKey 不该查到任何色")
    }

    // MARK: - 泳道拆分（R16）

    private func fixtureMap() -> ExposureMap {
        ExposureMap(
            sourceVersion: "V1.1",
            oldestReviewed: "2026-08-09",
            staleCount: 1,
            palette: [
                "deep_green": .init(key: "deep_green", label: "深绿", meaning: "底座 / 低暴露"),
                "light_green": .init(key: "light_green", label: "浅绿", meaning: "替代 / 政策主航道"),
                "yellow": .init(key: "yellow", label: "黄", meaning: "外需 / 全球定价"),
                "orange": .init(key: "orange", label: "橙", meaning: "许可 / 点名博弈"),
                "purple": .init(key: "purple", label: "紫", meaning: "反制筹码"),
            ],
            axes: [
                "six_networks": .init(label: "六张网", sourceRef: "5.2",
                                      groups: ["water": "水网", "compute": "算力网"]),
                "strategic_industries": .init(label: "十五五战略性新兴产业", sourceRef: "5.3",
                                              groups: ["infotech": "新一代信息技术"]),
            ],
            nodes: [
                node("water.01", "水利工程/设计", axis: "six_networks", group: "water", color: "orange"),
                node("water.02", "泵阀管材", axis: "six_networks", group: "water", color: "deep_green",
                     state: .confirmedEmpty),
                node("water.03", "水质监测治理", axis: "six_networks", group: "water", color: "pending"),
                node("water.04", "水务运营", axis: "six_networks", group: "water", color: "deep_green"),
                node("compute.01", "IDC/智算中心", axis: "six_networks", group: "compute",
                     color: "light_green", stocks: ["688017.SH"]),
                node("infotech.03", "先进代工/先进封装/存储", axis: "strategic_industries",
                     group: "infotech", color: "orange", secondary: "yellow",
                     qualifier: "源表标「橙→黄」", reading: "管制与周期双杀或双击", tier: "second"),
            ]
        )
    }

    private func node(
        _ id: String, _ name: String, axis: String, group: String, color: String,
        secondary: String = "", qualifier: String = "", reading: String = "",
        tier: String = "", state: ExposureNodeState = .unreviewed, stocks: [String] = []
    ) -> ExposureNode {
        ExposureNode(
            nodeId: id, name: name, axis: axis, group: group,
            primaryColor: color, secondaryColor: secondary, tier: tier,
            reading: reading, qualifier: qualifier, sourceRef: "5.x",
            lastReviewed: "2026-08-09", isPending: color == "pending",
            isStale: false, nodeState: stocks.isEmpty ? state : .hasStocks,
            confirmedAt: "", stocks: stocks
        )
    }

    func testLanesFollowYAMLOrderNotDictionaryOrder() {
        let sections = InvestabilityLaneBuilder.sections(map: fixtureMap())
        XCTAssertEqual(sections.map(\.title), ["六张网", "十五五战略性新兴产业"])
        XCTAssertEqual(sections[0].lanes.map(\.title), ["水网", "算力网"])
    }

    func testLaneNodesSortByColorWithPendingLast() {
        let sections = InvestabilityLaneBuilder.sections(map: fixtureMap())
        let water = sections[0].lanes[0]
        XCTAssertEqual(water.nodes.map(\.name),
                       ["泵阀管材", "水务运营", "水利工程/设计", "水质监测治理"],
                       "深绿在前、橙在后、未定色垫底；同色保持 YAML 原序")
    }

    /// 覆盖 AE5：未定色节点不归入任何色块，只出现在未定色区。
    func testPendingNodeStaysOutOfEveryColorBlock() {
        let map = fixtureMap()
        for key in ExposureFilter.paletteOrder {
            let names = map.nodes.filter { $0.primaryColor == key }.map(\.name)
            XCTAssertFalse(names.contains("水质监测治理"), "未定色节点混进了 \(key) 色块")
        }
        XCTAssertEqual(map.nodes.filter(\.isPending).map(\.name), ["水质监测治理"])
    }

    /// 覆盖 AE1：三种节点覆盖态在数据层就分得开（渲染分别是实线 / 空心 / 虚线）。
    func testNodeCoverageStatesAreDistinct() {
        let map = fixtureMap()
        let byId = Dictionary(uniqueKeysWithValues: map.nodes.map { ($0.nodeId, $0) })
        XCTAssertEqual(byId["compute.01"]?.state, .hasStocks)
        XCTAssertEqual(byId["water.02"]?.state, .confirmedEmpty)
        XCTAssertEqual(byId["water.04"]?.state, .unreviewed)
    }

    // MARK: - 节点展开区（AE7）

    /// 覆盖 AE7：复合色节点显示次色与限定说明，渲染色仍取主色。
    func testCompositeColorNodeShowsSecondaryAndQualifier() {
        let map = fixtureMap()
        guard let node = map.nodes.first(where: { $0.nodeId == "infotech.03" }) else {
            return XCTFail("夹具缺 infotech.03")
        }
        XCTAssertEqual(
            InvestabilityNodePanel.secondaryText(node: node, palette: map.palette),
            "次色 黄 · 源表标「橙→黄」")
        XCTAssertEqual(node.reading, "管制与周期双杀或双击")
        XCTAssertEqual(node.primaryColor, "orange", "次色不参与渲染，节点与其下个股都按主色走")
        XCTAssertEqual(node.tierBadge, "二线")
        let p = ThemeCatalog.palette(for: .clayM3, appearance: .dark)
        XCTAssertEqual(p.exposureColor(forKey: node.primaryColor), p.exposureOrange)
    }

    func testSecondaryTextOmittedWhenNeitherSecondaryColorNorQualifier() {
        let map = fixtureMap()
        let plain = map.nodes.first { $0.nodeId == "water.01" }!
        XCTAssertNil(InvestabilityNodePanel.secondaryText(node: plain, palette: map.palette))
    }

    /// 未定色节点不假装自己是某个色。
    func testPrimaryTextForPendingNodeDoesNotClaimAColor() {
        let map = fixtureMap()
        let pending = map.nodes.first(where: \.isPending)!
        let text = InvestabilityNodePanel.primaryText(node: pending, palette: map.palette)
        XCTAssertTrue(text.contains("待定色"), text)
        for color in map.palette.values {
            XCTAssertFalse(text.contains(color.label), "待定色文案不该出现「\(color.label)」")
        }
    }

    // MARK: - 按色筛选（R20）

    func testColorFilterMatchesByState() throws {
        let labelled = try decodeStock()
        let unlabelled = ExposureStock(
            tsCode: "600000.SH", state: .unlabelled, stateLabel: "未上图", colorKey: "",
            colorLabel: "", primaryNode: nil, secondaryNodes: [],
            zone: labelled.zone, answers: ExposureAnswers(), labelUpdatedAt: "", isStale: false)

        XCTAssertTrue(ExposureFilter.color("light_green").matches(labelled, loaded: true))
        XCTAssertFalse(ExposureFilter.color("orange").matches(labelled, loaded: true))
        XCTAssertTrue(ExposureFilter.unlabelled.matches(unlabelled, loaded: true))
        XCTAssertFalse(ExposureFilter.unlabelled.matches(labelled, loaded: true))
        XCTAssertTrue(ExposureFilter.all.matches(nil, loaded: true))
    }

    /// 字典没加载时筛选一律放行——过滤掉全部行会被读成「池子空了」。
    func testFilterPassesEverythingBeforeLabelsLoad() {
        XCTAssertTrue(ExposureFilter.color("purple").matches(nil, loaded: false))
        XCTAssertTrue(ExposureFilter.unlabelled.matches(nil, loaded: false))
    }

    /// 色板还没拉回来时筛选条也要有中文项，不能退化成机器键。
    func testFilterOptionsFallBackToChineseLabels() {
        let options = ExposureFilter.options(palette: [:])
        XCTAssertEqual(options.map(\.1), ["全部", "深绿", "浅绿", "黄", "橙", "紫", "待定色", "未上图"])
    }

    // MARK: - 未加载态（本轮审阅补的那条）

    /// `byCode == nil` 是独立一态：不是「全都未上图」。
    func testExposureContextDistinguishesNotLoadedFromUnlabelled() {
        let notLoaded = ExposureContext(byCode: nil)
        XCTAssertFalse(notLoaded.loaded)
        XCTAssertNil(notLoaded.stock("688017.SH"))

        let loaded = ExposureContext(byCode: [:])
        XCTAssertTrue(loaded.loaded)
        XCTAssertNil(loaded.stock("688017.SH"), "已加载但查无此票 = 未上图")
    }

    // MARK: - 8 问题面

    func testEightQuestionsMatchSourceText() {
        XCTAssertEqual(ExposureAnswers.questions.count, ExposureAnswers.questionCount)
        XCTAssertTrue(ExposureAnswers.questions[0].hasPrefix("收入"))
        XCTAssertTrue(ExposureAnswers.questions[7].hasPrefix("出境"))
    }
}
