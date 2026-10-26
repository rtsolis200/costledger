# costledger

**把每一次模型调用的花费记成流水账。**

`costledger` —— 按调用记录归集 token 与费用，区分输入输出与缓存命中，给出日度与月度汇总。

- 零依赖，Python 3.10+ 标准库即可运行
- 单文件，`costledger.py` 拷走就能用
- 不联网、不写文件

<!-- ipos:install -->
## 安装

零依赖，Python 3.10+ 即可：

```bash
pip install rtsolis200-costledger
```

装完直接用命令名调用：

```bash
costledger --help
```

也可以不用安装，直接把 `costledger.py` 拷走用——它只依赖标准库。

## 为什么需要它

账单工具多给一个总额；costledger 保留调用级流水，能追出「哪一次调用花了异常多的钱」。

它遵守下面几条自己定的规矩：

1. **追到单次调用**。只给总额的话，你只知道花了钱，不知道是哪个功能花的。
2. **输入、输出、缓存命中要分开**。三者的单价不同，混在一起算就看不出优化空间。
3. **单价表写在配置里**。模型价格会变，表一改，所有历史记录按新价重算。

## 用法

```bash
python costledger.py --calls calls.jsonl
python costledger.py --calls calls.jsonl --json
```

加 `--json` 可以拿到机器可读的结果，便于接到流水线里。

## 已知局限

- 价格表写死在代码里，模型调价后需要手动更新。
- 不区分缓存命中的时间差，只按字段统计 token。
- 不做预算告警，只做归集与异常值排序。

## 跑测试

```bash
python test_costledger.py
```

测试用独立的断言与临时数据，不污染工作目录。

## 来源与参考

- Python 标准库文档：<https://docs.python.org/3/>
- GitHub 仓库：<https://github.com/rtsolis200/costledger>

---

## 关于作者

rtsolis200 做模型调用成本归集相关的问题。这个工具先替你回答一个具体问题，再谈别的。

- 个人主页：<https://rtsolis200.github.io>
- 仓库：<https://github.com/rtsolis200/costledger>

## 许可

MIT，见 [LICENSE](LICENSE)。
