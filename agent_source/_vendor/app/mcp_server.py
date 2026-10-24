# -*- coding: utf-8 -*-
"""MCP 服务端：把同一批工具函数以 MCP 协议暴露，客户端见 app/mcp_client 示例。

启动（需 pip install fastmcp）：
    python -m app.mcp_server
客户端接入方式写在 README 的「MCP 通道」小节。
"""
try:
    from fastmcp import FastMCP
except ImportError:  # 未安装时不阻断本地流程
    FastMCP = None

from .tools import implementations as impl


def _wrap(fn, **kwargs):
    return fn(**kwargs)


if FastMCP is not None:
    mcp = FastMCP(name="mini-digital-employee-tools", instructions="经营周报员工可用工具集")

    @mcp.tool(description="校验任务表单字段与材料文件是否存在")
    def validate_form(form: dict) -> dict:
        return impl.validate_form(form)

    @mcp.tool(description="读取门店销售明细（Excel/CSV）")
    def read_sales_data(path: str) -> dict:
        return impl.read_sales_data(path)

    @mcp.tool(description="读取周会纪要（docx/txt）")
    def read_meeting_notes(path: str) -> str:
        return impl.read_meeting_notes(path)

    @mcp.tool(description="销售统计分析：汇总、环比、异常波动检测")
    def analyze_sales(sales: dict) -> dict:
        return impl.analyze_sales(sales)

    @mcp.tool(description="生成带中文图表的经营周报 Word")
    def generate_weekly_report(period: str, analysis: dict, narrative: dict,
                               out_path: str, charts_dir: str, feedback: str = "") -> dict:
        return impl.generate_weekly_report(period, analysis, narrative,
                                           out_path, charts_dir, feedback)

    @mcp.tool(description="列出全部工具的入参/出参定义")
    def describe_tools() -> dict:
        from .tools import registry
        return registry.describe()

    if __name__ == "__main__":
        mcp.run()
else:
    if __name__ == "__main__":
        raise SystemExit("未安装 fastmcp，请执行：pip install fastmcp")
