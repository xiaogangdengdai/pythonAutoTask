#!/usr/bin/env python3
"""
自动化任务执行器（轮询模式）
持续运行，每隔1分钟检测是否有待处理的系统问题日志
检测到后启动Claude处理，处理完成后继续检测下一条
"""

import subprocess
import os
import re
import time
import signal
from datetime import datetime
from pathlib import Path

# 配置
SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "outputs"
LOG_FILE = SCRIPT_DIR / "auto_task.log"
CHECK_INTERVAL = 60  # 检测间隔（秒）
CLAUDE_TIMEOUT = 1800  # Claude执行超时时间（30分钟）

# ============================================================
# 第一阶段：提取日志信息的提示词模板
# ============================================================
STAGE1_EXTRACT_PROMPT = """请执行以下操作，不要向我确认任何事情：

1. 使用 mcp__mcp-server-demo__systemlog_getsystemlog 工具获取1条系统问题日志

2. 从返回的 <referenceInfo> 标签中提取以下信息，并严格按照以下格式输出：

<extracted>
<id>提取的日志ID</id>
<type>提取的type值</type>
<createTableSql>提取的建表语句内容</createTableSql>
<businessContext>提取的业务背景内容</businessContext>
<description>提取的问题详细描述内容</description>
<newRequirement>提取的新需求内容</newRequirement>
<beforeTransformation>提取的改造前信息</beforeTransformation>
<transformation>提取的改造目标信息</transformation>
<attachmentPaths>提取的附件路径列表</attachmentPaths>
</extracted>

注意：如果某个标签在原始数据中为空，则输出空标签，例如：<newRequirement></newRequirement>
"""

# ============================================================
# 第二阶段：根据type类型的提示词模板
# ============================================================

TEMPLATE_BUG_FIX = """<createTableSql>
{createTableSql}
</createTableSql>

<businessContext>
{businessContext}
</businessContext>

<attachmentPaths>
{attachmentPaths}
</attachmentPaths>

<createTableSql>标签内的内容是数据库建表语句，
<businessContext>标签内的内容是bug修复的要求，
<attachmentPaths>标签内的内容是相关附件文件的路径，

你是一个有着20多年开发经验的开发专家和业务分析专家以及数据库设计专家，
必须首先认真读取: /home/wzh/workspace/CLAUDE.md 这个文件，这个文件是氧屋系统的整体业务和技术介绍。
其他业务相关的资料位于: /home/wzh/workspace/AIGC/result 文件夹下，按需进行搜索。
帮我结合上面的背景信息，完成bug修复。需要在本地新建一个fix分支。

执行过程中请直接采用最佳方案，不需要任何确认。
"""

TEMPLATE_NEW_FEATURE = """<createTableSql>
{createTableSql}
</createTableSql>

<businessContext>
{businessContext}
</businessContext>

<newRequirement>
{newRequirement}
</newRequirement>

<attachmentPaths>
{attachmentPaths}
</attachmentPaths>

<createTableSql>标签内的内容是数据库建表语句，
<businessContext>标签内的内容是业务背景介绍，
<newRequirement>标签内的内容是新需求，例如文件位置，代码位置，改造的实现思路，
<attachmentPaths>标签内的内容是相关附件文件的路径，

你是一个有着20多年开发经验的开发专家和业务分析专家以及数据库设计专家，
必须首先认真读取: /home/wzh/workspace/CLAUDE.md 这个文件，这个文件是氧屋系统的整体业务和技术介绍。
其他业务相关的资料位于: /home/wzh/workspace/AIGC/result 文件夹下，按需进行搜索。
帮我结合上面的背景信息，完成新功能的开发任务。需要在本地新建一个feature分支。

执行过程中请直接采用最佳方案，不需要任何确认。
"""

TEMPLATE_REFACTOR = """<createTableSql>
{createTableSql}
</createTableSql>

<businessContext>
{businessContext}
</businessContext>

<beforeTransformation>
{beforeTransformation}
</beforeTransformation>

<transformation>
{transformation}
</transformation>

<attachmentPaths>
{attachmentPaths}
</attachmentPaths>

<createTableSql>标签内的内容是数据库建表语句，
<businessContext>标签内的内容是业务背景介绍，
<beforeTransformation>标签内是改造前的相关信息，例如文件位置，代码位置，改造前的实现思路，
<transformation>标签内是需要完成改造后的最终目标信息，例如文件位置，代码位置，改造的实现思路，
<attachmentPaths>标签内的内容是相关附件文件的路径，

你是一个有着20多年开发经验的开发专家和业务分析专家以及数据库设计专家，
必须首先认真读取: /home/wzh/workspace/CLAUDE.md 这个文件，这个文件是氧屋系统的整体业务和技术介绍。
其他业务相关的资料位于: /home/wzh/workspace/AIGC/result 文件夹下，按需进行搜索。
帮我结合上面的背景信息，完成原有功能改造的开发任务。需要在本地新建一个feature分支。

执行过程中请直接采用最佳方案，不需要任何确认。
"""

TEMPLATE_PROTOTYPE = """<createTableSql>
{createTableSql}
</createTableSql>

<description>
{description}
</description>

<attachmentPaths>
{attachmentPaths}
</attachmentPaths>

<createTableSql>标签内的内容是数据库建表语句，
<description>标签内的内容是业务需求描述，
<attachmentPaths>标签内的内容是相关附件文件的路径，

业务背景：由于氧屋系统最近有一些功能需要紧急上线。走正常的产品经理进行需求分析-》画页面原型图-》开发人员按照需求和页面原型图进行开发，这种常规流程已经无法满足业务要求。老板要求开发人员根据产品经理的<description>标签中的需求描述，迅速生成可操作并模拟真实操作的html页面，产品经理打开html页面，可以直接操作，并提出修改意见。最终开发人员完全按照html页面的逻辑去开发对应的功能。由于开发人员和产品经理两地办公，并且网络不通，产品经理没有任何技术背景，只能通过html页面来进行业务的沟通。

现在请基于上面提供的所有信息进行详细的分析，最终生成html页面。具体要求如下：

1.页面样式布局及实现思路参考：/home/wzh/workspace/AIGC/html/人员管理系统.html，左侧是功能菜单，右侧是具体的功能页面。

2.在内存中模拟<createTableSql>标签中建表语句对应的数据库表的数据结构，数据结构和建表语句要完全保持一致。通过JavaScript程序模拟mysql数据库表的增删改查操作、多表关联查询操作、union操作等最常用和基本的功能。

3.在页面最下方有一个控制台，用户的每一步操作都会将对应的mysql的sql语句追加打印到控制台中。包括新增、修改、删除、单表查询、多表关联查询。

4.最终将生成的html页面内容，保存到 /home/wzh/codes/uploadFiles/{issue_id}/ 路径下，文件名为随机36位UUID.html（例如：a1b2c3d4-e5f6-7890-abcd-ef1234567890.html）

5.生成完成后，使用 mcp__mcp-server-demo__systemlog_savesystemattachment 工具将附件信息保存到sys_attachment表中：
   - targetId: {issue_id}
   - filePath: 生成的html文件完整路径
   - fileName: 生成的html文件名
   - sortOrder: 0

执行过程中请直接采用最佳方案，不需要任何确认。
"""

# Type 到模板的映射
PROMPT_TEMPLATES = {
    1: TEMPLATE_BUG_FIX,      # bug修复
    2: TEMPLATE_NEW_FEATURE,  # 新功能开发
    3: TEMPLATE_REFACTOR,     # 原有功能改造
    4: TEMPLATE_PROTOTYPE,    # 页面原型快速实现
}

# 全局运行标志
running = True

def signal_handler(signum, frame):
    """信号处理器，优雅退出"""
    global running
    log(f"收到信号 {signum}，准备退出...")
    running = False

def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    print(log_line, flush=True)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")

def parse_extracted_tags(text: str) -> dict:
    """
    从Claude返回的文本中解析 <extracted> 标签内的数据

    Args:
        text: Claude返回的文本

    Returns:
        包含各标签内容的字典
    """
    result = {
        'id': '',
        'type': '',
        'createTableSql': '',
        'businessContext': '',
        'description': '',
        'newRequirement': '',
        'beforeTransformation': '',
        'transformation': '',
        'attachmentPaths': ''
    }

    # 首先提取 <extracted> 标签内的内容
    extracted_match = re.search(r'<extracted>(.*?)</extracted>', text, re.DOTALL)
    if not extracted_match:
        log("未找到 <extracted> 标签")
        return result

    extracted_content = extracted_match.group(1)

    # 定义需要提取的标签
    tags = [
        'id', 'type', 'createTableSql', 'businessContext', 'description',
        'newRequirement', 'beforeTransformation', 'transformation', 'attachmentPaths'
    ]

    # 提取每个标签的内容
    for tag in tags:
        pattern = rf'<{tag}>(.*?)</{tag}>'
        match = re.search(pattern, extracted_content, re.DOTALL)
        if match:
            result[tag] = match.group(1).strip()

    # 转换type为整数
    try:
        result['type'] = int(result['type']) if result['type'] else 0
    except ValueError:
        result['type'] = 0

    log(f"解析结果: type={result['type']}, id={result['id'][:20]}..." if result['id'] else f"解析结果: type={result['type']}")

    return result

def generate_prompt_by_type(issue_type: int, data: dict) -> str:
    """
    根据问题类型生成对应的提示词

    Args:
        issue_type: 问题类型 (1=bug修复, 2=新功能, 3=改造)
        data: 从日志中提取的数据字典

    Returns:
        生成的提示词字符串，如果类型不支持返回 None
    """
    template = PROMPT_TEMPLATES.get(issue_type)

    if not template:
        log(f"不支持的type类型: {issue_type}")
        return None

    # 填充模板
    prompt = template.format(
        createTableSql=data.get('createTableSql', ''),
        businessContext=data.get('businessContext', ''),
        description=data.get('description', ''),
        newRequirement=data.get('newRequirement', ''),
        beforeTransformation=data.get('beforeTransformation', ''),
        transformation=data.get('transformation', ''),
        attachmentPaths=data.get('attachmentPaths', ''),
        issue_id=data.get('id', '')
    )

    log(f"已生成type={issue_type}的提示词，长度: {len(prompt)} 字符")
    return prompt

def update_issue_status(issue_id: str, status: int, ai_response: str = "") -> bool:
    """
    更新问题日志状态

    Args:
        issue_id: 问题日志ID
        status: 状态 (3=已完成, 4=失败)
        ai_response: AI反馈内容

    Returns:
        是否更新成功
    """
    update_prompt = f"""请执行以下操作，不要向我确认任何事情：

使用 mcp__mcp-server-demo__systemlog_updatesystemlog 工具更新问题日志状态：
- id: {issue_id}
- status: {status}
- aiResponse: {ai_response[:500] if ai_response else '已通过自动化任务处理'}

执行完成后回复: UPDATE_DONE
"""

    result = run_claude_with_prompt(update_prompt)

    if result[0] == 0 and "UPDATE_DONE" in result[1]:
        log(f"状态更新成功: id={issue_id}, status={status}")
        return True
    else:
        log(f"状态更新失败: id={issue_id}")
        return False

def check_pending_issue() -> bool:
    """
    使用MCP工具检测是否有待处理的问题日志
    返回 True 表示有待处理的日志
    """
    log("检测是否有待处理的问题日志...")

    # 通过调用 Claude 快速检测（使用简化的prompt）
    check_prompt = """
请只执行这一个操作，不要做其他任何事情：
使用 mcp__mcp-server-demo__systemlog_getsystemlog 工具获取1条系统问题日志。

如果成功获取到日志（返回内容包含<referenceInfo>），请回复: HAS_ISSUE
如果没有待处理的日志，请回复: NO_ISSUE
"""

    cmd = [
        "claude",
        "--permission-mode", "bypassPermissions",
        "--print",
        check_prompt
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2分钟超时用于检测
        )

        output = result.stdout.strip()
        log(f"检测结果: {output[:200]}..." if len(output) > 200 else f"检测结果: {output}")

        # 判断是否有待处理的日志
        if "HAS_ISSUE" in output or "<referenceInfo>" in output:
            log("发现待处理的问题日志")
            return True
        else:
            log("当前没有待处理的问题日志")
            return False

    except subprocess.TimeoutExpired:
        log("检测超时，假设无待处理日志")
        return False
    except Exception as e:
        log(f"检测出错: {e}")
        return False

def run_claude_with_prompt(prompt: str) -> tuple:
    """
    以非交互方式运行Claude Code处理问题日志

    Returns:
        (return_code, stdout, stderr)
    """
    log("启动Claude Code处理问题日志...")

    cmd = [
        "claude",
        "--permission-mode", "bypassPermissions",
        "--print",
        prompt
    ]

    log(f"执行命令: claude --permission-mode bypassPermissions --print <prompt>")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Claude执行超时（{CLAUDE_TIMEOUT}秒）"
    except Exception as e:
        return -1, "", str(e)

def process_one_issue():
    """处理一条问题日志（两阶段模式）"""
    try:
        # ============================================================
        # 第一阶段：获取日志信息
        # ============================================================
        log("=" * 40)
        log("第一阶段：获取日志信息")
        log("=" * 40)

        return_code, stdout, stderr = run_claude_with_prompt(STAGE1_EXTRACT_PROMPT)

        if return_code != 0:
            log(f"第一阶段执行失败，返回码: {return_code}")
            if stderr:
                log(f"错误输出: {stderr}")
            return

        # 保存第一阶段输出
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stage1_file = OUTPUT_DIR / f"stage1_{timestamp}.txt"
        with open(stage1_file, "w", encoding="utf-8") as f:
            f.write(stdout)
        log(f"第一阶段输出已保存到: {stage1_file}")

        # 解析提取的数据
        data = parse_extracted_tags(stdout)

        if not data['id']:
            log("未能提取日志ID，跳过处理")
            return

        # ============================================================
        # 检查type是否支持
        # ============================================================
        issue_type = data['type']
        if issue_type not in PROMPT_TEMPLATES:
            log(f"不支持的type类型: {issue_type}")
            update_issue_status(data['id'], 4, f"不支持的type类型: {issue_type}，待扩展实现")
            return

        # ============================================================
        # 第二阶段：执行开发任务
        # ============================================================
        log("=" * 40)
        log(f"第二阶段：执行开发任务 (type={issue_type})")
        log("=" * 40)

        # 生成提示词
        prompt = generate_prompt_by_type(issue_type, data)
        if not prompt:
            update_issue_status(data['id'], 4, f"生成提示词失败")
            return

        # 执行第二阶段
        return_code, stdout, stderr = run_claude_with_prompt(prompt)

        # 保存第二阶段输出
        stage2_file = OUTPUT_DIR / f"stage2_{timestamp}.txt"
        with open(stage2_file, "w", encoding="utf-8") as f:
            f.write(stdout if stdout else "")
            if stderr:
                f.write(f"\n\n=== STDERR ===\n{stderr}")
        log(f"第二阶段输出已保存到: {stage2_file}")

        # ============================================================
        # 更新状态
        # ============================================================
        if return_code == 0:
            log("开发任务执行成功")
            update_issue_status(data['id'], 3, "已通过自动化任务处理完成")
        else:
            log(f"开发任务执行失败，返回码: {return_code}")
            update_issue_status(data['id'], 4, f"任务执行失败: {stderr[:200] if stderr else '未知错误'}")

    except Exception as e:
        log(f"处理出错: {e}")
        import traceback
        log(traceback.format_exc())

def main():
    """主函数 - 轮询模式"""
    # 注册信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log("=" * 60)
    log("自动化任务执行器启动（轮询模式）")
    log(f"检测间隔: {CHECK_INTERVAL}秒, Claude超时: {CLAUDE_TIMEOUT}秒")
    log("=" * 60)

    try:
        # 确保输出目录存在
        OUTPUT_DIR.mkdir(exist_ok=True)

        while running:
            try:
                # 检测是否有待处理的问题日志
                has_issue = check_pending_issue()

                if has_issue:
                    # 有待处理的日志，启动Claude处理
                    log("-" * 40)
                    log("开始处理问题日志")
                    log("-" * 40)
                    process_one_issue()
                    log("-" * 40)
                    log("问题日志处理完成，继续检测...")
                    log("-" * 40)
                    # 处理完立即继续检测，不等待
                else:
                    # 没有待处理的日志，等待后再次检测
                    log(f"等待 {CHECK_INTERVAL} 秒后再次检测...")
                    for _ in range(CHECK_INTERVAL):
                        if not running:
                            break
                        time.sleep(1)

            except Exception as e:
                log(f"轮询循环出错: {e}")
                import traceback
                log(traceback.format_exc())
                # 出错后等待一会再继续
                time.sleep(60)

    except KeyboardInterrupt:
        log("用户中断")
    except Exception as e:
        log(f"严重错误: {e}")
        import traceback
        log(traceback.format_exc())
        return 1

    log("自动化任务执行器退出")
    return 0

if __name__ == "__main__":
    exit(main())
