# 字体来源与许可

`NotoSansSC-subset.otf` 来自 [Noto Sans CJK SC](https://github.com/notofonts/noto-cjk)
（思源黑体同源），按 [SIL Open Font License 1.1](https://openfontlicense.org/) 授权，
可随项目自由分发、嵌入与再分发。

子集化说明：仅保留 GB2312 常用汉字、ASCII 与常用标点（约 6800 字形），
原字体 15.6 MB → 子集 2.9 MB，用于在没有中文字体的 Linux 容器 /
EdgeOne Makers 云函数里保证图表中文标签正常渲染。

再生成方式（本机装有 fonttools 时）：

```bash
python -m fontTools.subset NotoSansCJKsc-Regular.otf \
    --text-file=chars.txt --output-file=NotoSansSC-subset.otf \
    --no-hinting
```
