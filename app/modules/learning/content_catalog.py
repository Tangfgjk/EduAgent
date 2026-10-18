"""版本化开发内容包注册表；年级是开发编排建议，未做教材/课标专业映射。"""
from dataclasses import dataclass
import hashlib
import json

from app.modules.errors import DomainError
from app.modules.learning import development_content as original


@dataclass(frozen=True)
class ContentPack:
    unit_id: str
    title: str
    grade: str
    domain: str
    knowledge_points: tuple[str, ...]
    prerequisites: tuple[str, ...]
    tasks: list[dict]
    version: str
    boundary: str = "只核验有理数结果；步骤、证明、素养与教学效果尚未评定。"

    def task_public(self, index):
        task = self.tasks[index]
        return {"task_id": task["id"], "stem": task["stem"], "answer_format": task["format"]}


def task(identifier, stage, stem, answer, hints=(), misconception=""):
    return {"id": identifier, "stage": stage, "stem": stem, "answer": answer,
            "format": "只填数值或分数，单位、步骤与依据写在下方。检测提交后不能修改，不提供提示。" if stage != "practice" else "只填数值或分数，步骤与依据写在下方。",
            "hints": list(hints), "misconception": misconception}


def pack(unit_id, title, grade, domain, points, prerequisites, tasks):
    # 教学元数据与题目一起锁定，修改先修关系或误区也需重新审核。
    body = {"unit_id": unit_id, "title": title, "grade": grade, "domain": domain,
            "knowledge_points": points, "prerequisites": prerequisites, "tasks": tasks}
    version = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return ContentPack(unit_id, title, grade, domain, tuple(points), tuple(prerequisites), tasks, version)


PACKS = (
    ContentPack(original.UNIT_ID, original.TITLE, "七年级", "数与式", ("等式性质", "一元一次方程", "数量关系建模"), (), original.TASKS, original.CONTENT_VERSION),
    pack("math.g7.rational.dev1", "有理数运算 · 开发单元", "七年级", "数与式", ["有理数加减", "有理数乘除", "运算顺序"], [], [
        task("D-RAT-01", "diagnostic", "计算 −3 + 8。", "5"),
        task("P-RAT-01", "practice", "计算 −6 ÷ 3 + 4，说明运算顺序。", "2", ["观察算式里有哪些运算。", "先算除法，再算加法。", "−6÷3=−2，再计算 −2+4。", "−6÷3+4=−2+4=2。异号两数相除得负，随后再相加。"], "把负数相除的结果当成正数，或先加后除。"),
        task("P-RAT-02", "practice", "上午气温 −2℃，中午升高 7℃，晚上又降低 3℃。晚上气温是多少摄氏度？", "2", ["用正负数表示升高和降低。", "从 −2 出发，先加 7，再减 3。", "列式 −2+7−3，中午为 5℃。", "−2+7−3=5−3=2，晚上为 2℃。可用数轴核对方向。"], "把降低也记为加法。"),
        task("C-RAT-01", "checkpoint", "独立检测：计算 −12 ÷ 4 + 8。", "5"),
        task("C-RAT-02", "checkpoint", "独立检测：气温 −4℃，升高 9℃后再降低 2℃，最终气温是多少摄氏度？", "3"),
    ]),
    pack("math.g8.linear.dev1", "一次函数求值 · 开发单元", "八年级", "函数", ["自变量与函数值", "代入求值"], [original.UNIT_ID], [
        task("D-LIN-01", "diagnostic", "一次函数 y=2x+1，当 x=3 时，y 是多少？", "7"),
        task("P-LIN-01", "practice", "一次函数 y=−2x+5，当 x=−1 时，求 y 并说明代入过程。", "7", ["先找出自变量 x 的给定值。", "把表达式中的 x 替换为 −1，注意括号。", "y=−2×(−1)+5，先算乘法。", "y=−2×(−1)+5=2+5=7。负数相乘得正。"], "漏掉负数代入时的括号或符号。"),
        task("P-LIN-02", "practice", "费用模型 y=3x+4，其中 x 表示购买件数，y 表示总费用（元）。购买 5 件时费用是多少元？", "19", ["确认 x 与 y 分别代表什么。", "把 x=5 代入已有模型。", "y=3×5+4，固定费用只加一次。", "y=3×5+4=15+4=19 元。这里使用给定模型，不自动评定建模能力。"], "将固定费用也乘以件数。"),
        task("C-LIN-01", "checkpoint", "独立检测：y=−3x+2，当 x=−2 时，y 是多少？", "8"),
        task("C-LIN-02", "checkpoint", "独立检测：费用模型 y=4x+6，购买 3 件时费用是多少元？", "18"),
    ]),
    pack("math.g8.pythagoras.dev1", "勾股定理计算 · 开发单元", "八年级", "几何", ["识别直角三角形的斜边", "勾股定理求边长"], ["math.g7.rational.dev1"], [
        task("D-PYT-01", "diagnostic", "直角三角形两条直角边分别长 3、4，斜边长多少？", "5"),
        task("P-PYT-01", "practice", "直角三角形两条直角边分别长 6、8，求斜边长，写出所用关系。", "10", ["先区分直角边和斜边。", "设斜边为 c，使用 c²=a²+b²。", "c²=6²+8²=36+64=100，边长取正值。", "c²=100，c=10。检查 6²+8²=10²。这是数值计算，不是定理证明。"], "把两条直角边直接相加作为斜边。"),
        task("P-PYT-02", "practice", "直角三角形斜边长 13，一条直角边长 5，另一条直角边长多少？", "12", ["已知的是斜边还是两条直角边？", "设另一条直角边为 b，5²+b²=13²。", "b²=169−25=144，边长取正值。", "b=12。检查 5²+12²=13²，不能把 13 与 5 的平方相加。"], "求直角边时仍把两个已知长度的平方相加。"),
        task("C-PYT-01", "checkpoint", "独立检测：直角三角形两条直角边长 9、12，斜边长多少？", "15"),
        task("C-PYT-02", "checkpoint", "独立检测：直角三角形斜边长 10，一条直角边长 6，另一条直角边长多少？", "8"),
    ]),
    pack("math.g9.probability.dev1", "等可能事件概率 · 开发单元", "九年级", "统计概率", ["等可能结果计数", "概率的分数表示"], ["math.g7.rational.dev1"], [
        task("D-PRB-01", "diagnostic", "掷一枚各面朝上等可能的六面骰子一次，点数是 6 的概率是多少？", "1/6"),
        task("P-PRB-01", "practice", "掷一枚各面朝上等可能的六面骰子一次，点数为偶数的概率是多少？", "1/2", ["先列出全部可能的点数。", "1 到 6 中哪些是偶数？", "偶数为 2、4、6，共 3 个；总结果共 6 个。", "概率=3/6=1/2。只在各个基本结果等可能的前提下这样计数。"], "把偶数的数值之和当作有利结果个数。"),
        task("P-PRB-02", "practice", "袋中有 3 个红球、2 个蓝球，球除颜色外完全相同。随机摸出一个，每个球被摸出的机会相同。摸到蓝球的概率是多少？", "2/5", ["确认总球数及有利结果对应的颜色。", "每个球是一个等可能的基本结果，不能只按颜色种类计数。", "总球数 3+2=5，蓝球有 2 个。", "摸到蓝球的概率=2/5。两种颜色不代表两个等可能结果。"], "有两种颜色就认为概率各为 1/2。"),
        task("C-PRB-01", "checkpoint", "独立检测：掷一枚各面朝上等可能的六面骰子一次，点数大于 4 的概率是多少？", "1/3"),
        task("C-PRB-02", "checkpoint", "独立检测：袋中有 4 个白球、3 个黑球，除颜色外完全相同，各球摸到机会相同。随机摸一个是黑球的概率是多少？", "3/7"),
    ]),
    pack('math.g9.probability.counting.dev1', '概率计数与事件 · 扩展开发单元', '九年级', '统计概率', ['列举基本结果', '事件计数', '补事件'], ['math.g9.probability.dev1'], [
        task('D-PCO-01', 'diagnostic', '四张卡片分别写有 1、2、3、4，随机等可能抽一张，抽到 4 的概率是多少？', '1/4'),
        task('P-PCO-01', 'practice', '八张卡片分别写有 1 至 8，随机等可能抽一张，抽到 3 的倍数的概率是多少？', '1/4', ['先列出所有可能的卡片。', '判断 1 至 8 中哪些数是 3 的倍数。', '符合条件的是 3 和 6，共 2 个；总数为 8。', '概率为 2/8=1/4。基本结果按卡片计数，不按数的类别计数。'], '把倍数类别误作一个等可能结果。'),
        task('P-PCO-02', 'practice', '袋中 2 个红球、3 个蓝球，各球摸到机会相同，随机摸一个不是红球的概率是多少？', '3/5', ['先说明“不是红球”包含哪些球。', '这里只有两种颜色，不是红球就是蓝球。', '符合条件的有 3 个，总共有 5 个。', '概率为 3/5，也可以用 1−2/5 核对。'], '把不是红球理解为没有球。'),
        task('C-PCO-01', 'checkpoint', '独立检测：卡片写有 1 至 10，随机等可能抽一张，抽到大于 7 的数的概率是多少？', '3/10'),
        task('C-PCO-02', 'checkpoint', '独立检测：6 个球中有 1 个红球、5 个白球，各球等可能被摸到。随机摸一个不是白球的概率是多少？', '1/6'),
    ]),
    pack('math.g7.statistics.frequency.dev1', '平均数与频率 · 扩展开发单元', '七年级', '统计概率', ['算术平均数', '中位数', '频率与概率的区别'], [], [
        task('D-STA-01', 'diagnostic', '数据 2、4、6 的算术平均数是多少？', '4'),
        task('P-STA-01', 'practice', '一组已从小到大排列的数据为 3、4、6、9、12，中位数是多少？', '6', ['中位数关注排序后的位置。', '这组数据已有从小到大的顺序。', '共有 5 个数据，中间是第 3 个。', '第 3 个数据是 6，所以中位数是 6，不是求总和。'], '把中位数当作平均数。'),
        task('P-STA-02', 'practice', '掷骰子 20 次，其中点数 6 出现 4 次。本次实验点数 6 出现的频率是多少？只求实验频率。', '1/5', ['频率描述这次实验的实际记录。', '用某结果出现的次数除以总实验次数。', '出现 4 次，总共 20 次，列式 4/20。', '4/20=1/5。这是本次实验频率，不据此改写均匀骰子的理论概率 1/6。'], '直接写理论概率而忽略实验记录。'),
        task('C-STA-01', 'checkpoint', '独立检测：数据 3、5、7 的算术平均数是多少？', '5'),
        task('C-STA-02', 'checkpoint', '独立检测：某事件在 30 次实验中出现 9 次，该事件在本次实验中的频率是多少？', '3/10'),
    ]),
)


def get_pack(unit_id=original.UNIT_ID, session=None):
    from app.modules.learning.content_repository import ContentRepository
    return ContentRepository(session, include_private=True).pack(unit_id)


def pack_for_task(task_id, version, session=None):
    from app.modules.learning.content_repository import ContentRepository
    for item in ContentRepository(session, include_private=True).packs():
        if item.version == version and any(task["id"] == task_id for task in item.tasks):
            return item
    for activity in ContentRepository(session, include_private=True).activities():
        if activity.parent.version == version and activity.verification_task and activity.verification_task['id'] == task_id:
            return activity.parent
    raise DomainError(409, "source_version_conflict", "内容版本已变化，不能自动执行此历史任务")
