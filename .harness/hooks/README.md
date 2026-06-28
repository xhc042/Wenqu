# `.harness/hooks/` 安装说明

## 这是什么

`post-commit` 是 git post-commit hook,在 `git commit` 完成后自动触发 verifier 检查。

**注意**:本仓库的 `.git/` 不入仓,hook 脚本放在 `.harness/hooks/` 入仓,需要手动安装一次。

## 安装(选其一)

### 软链接(推荐,升级自动同步)

git bash / Linux / macOS:
```bash
ln -s ../../.harness/hooks/post-commit .git/hooks/post-commit
```

Windows PowerShell(需要管理员或 Developer Mode):
```powershell
New-Item -ItemType SymbolicLink `
  -Path .git/hooks/post-commit `
  -Target ../../.harness/hooks/post-commit
```

### 复制(简单粗暴)

```bash
cp .harness/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit   # Linux/macOS
```

## 跳过机制

```bash
git commit -m "wip [skip-hook]"
```

## 行为说明

- 永远 exit 0,不阻塞 commit
- 同步跑 `pytest -m "not slow"`,超时 120s
- 打印对应文件的 review checklist
- 写入 `.harness/.last-commit.json`,Mavis 下次会话会读取

## 为什么不在 hook 里 spawn worker

Mavis runtime 的 spawn 通道是 **verifier-only**:
- ✅ 可 spawn:`tester` / `code-reviewer`
- ❌ 不可 spawn:`developer` / `prompt-engineer` / `db-migrator` / `reading-mentor`(producer 类型)

git hook 是无 session 的匿名脚本,无法可靠 spawn verifier(没有真实 session id)。
所以 hook 走务实路线:**同步跑测试 + 打印 checklist + 留日志**,等 Mavis 下次主动会话再决定要不要 spawn verifier。

## 验证安装

```bash
# 改任意文件然后 commit,看到 [hook] 开头的输出即成功
echo "# test" >> README.md
git add README.md
git commit -m "test post-commit hook"
# 应该看到 [hook] running pytest ... [hook] log -> .harness/.last-commit.json
```

如果没生效,检查:
- `.git/hooks/post-commit` 是否存在并有执行权限
- git 版本是否支持 post-commit hook(git ≥ 2.9)
- Windows 下 `.git/hooks/` 路径是否正确