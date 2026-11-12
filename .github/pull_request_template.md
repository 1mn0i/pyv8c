## 变更目的

<!-- 关联 issue：#123 -->

## 做了什么

## 如何验证

```bash
python -m pytest -q
python tools/check_policy.py
```

结果：

## 检查清单

- [ ] 已阅读 [贡献指南](https://github.com/ConlinH/pyv8c/blob/main/.github/CONTRIBUTING.md) 与 [行为准则](https://github.com/ConlinH/pyv8c/blob/main/.github/CODE_OF_CONDUCT.md)
- [ ] 新增或修改的逻辑配有回归测试，且测试通过
- [ ] `python tools/check_policy.py` 六道门通过
- [ ] 变更不涉及绕过第三方站点访问控制、规避反自动化措施或批量采集真实站点数据
- [ ] 未附带真实站点的凭据、Cookie、挑战令牌、HAR 或私有脚本
- [ ] 如改动第三方组件或打包元数据，已同步更新 `THIRD_PARTY_NOTICES.md`
- [ ] 提交已使用 `git commit -s` 添加 `Signed-off-by`
- [ ] 新增 `web/**` 实现已同批改测试（companions 门）

> 检查清单是贡献流程要求，不改变 MIT 许可证授予的权利。
