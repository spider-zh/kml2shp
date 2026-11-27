# KML / SHP 转换器（Kivy Android 版）

本项目把原来的 Tkinter 桌面版拆分为：

```text
kivy_android/
├── main.py              # Kivy 界面（Android / Windows 都能运行）
├── geo_core.py          # KML/KMZ <-> SHP 转换核心（纯标准库）
├── buildozer.spec       # Buildozer Android 打包配置
├── sample.kml           # 示例数据
└── assets/
    ├── icon.png
    └── NotoSansCJKsc-Regular.otf
```

## 功能

- 打开 KML/KMZ、SHP，并在地图上显示点/线/面。
- 点击地图要素或左侧列表查看字段属性。
- 图层显示/隐藏、缩放、平移、适应视图。
- 导出 KML、KMZ、SHP（SHP 自动生成 .shp/.shx/.dbf/.prj/.cpg）。
- Android 使用 androidstorage4kivy 调用系统文件选择器（SAF），兼容 Android 10+ 分区存储。
- 导出文件保存到 下载/KMLShpConverter/。
- 内嵌 Noto Sans CJK 字体，避免中文显示为方框。

## Windows 本地运行

```powershell
python -m pip install kivy==2.3.1
cd kivy_android
python main.py
```

androidstorage4kivy 只在 Android 运行时导入，Windows 上不需要安装。

## 打包 APK

Buildozer 官方不支持 Windows，请在 WSL2 Ubuntu 22.04 或 GitHub Actions 中执行。

### 1. WSL2 安装 Ubuntu

管理员 PowerShell：

```powershell
wsl --install -d Ubuntu-22.04
```

### 2. 安装系统依赖

在 Ubuntu 中：

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  python3 python3-pip python3-venv git zip unzip \
  openjdk-17-jdk autoconf automake libtool pkg-config \
  zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo6 \
  cmake libffi-dev libssl-dev libsqlite3-dev \
  build-essential ccache
python3 -m pip install --user --upgrade pip
python3 -m pip install --user buildozer cython==0.29.36
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
```

### 3. 复制项目到 Linux 家目录

不要直接在 /mnt/c/... 中构建：

```bash
mkdir -p ~/kml_shp_android
cp -r /mnt/c/Users/你的用户名/项目路径/kivy_android/* ~/kml_shp_android/
cd ~/kml_shp_android
```

### 4. 构建调试 APK

```bash
buildozer -v android debug
```

生成的 APK 在：

```text
bin/kmlshpconverter-1.0.0-arm64-v8a-debug.apk
```

首次构建会下载 Android SDK/NDK，可能需要 20-60 分钟。

### 5. 安装到手机

把 APK 复制到手机点击安装，或使用 adb：

```powershell
adb install -r \\wsl$\Ubuntu-22.04\home\你的用户名\kml_shp_android\bin\kmlshpconverter-1.0.0-arm64-v8a-debug.apk
```

## Android 文件选择说明

- 打开 KML/KMZ：点"打开KML"，选择一个 .kml 或 .kmz。
- 打开 SHP：点"打开SHP"，建议同时选中 .shp、.dbf、.shx、.prj、.cpg 五个文件；至少选择 .shp。
- 选中的文件会先复制到应用缓存目录再解析。
- 导出会写入系统"下载/KMLShpConverter"目录。

## 已知限制

- 坐标按 WGS84 经纬度处理，不做投影转换。
- DBF 字段名最多 10 字节、字符值最多 254 字节，超长会截断。
- KML 中混合点/线/面导出 SHP 时，会自动拆分成多个 Shapefile。
- 地图为离屏经纬网预览，不含在线底图。

## 使用 GitHub Actions 自动打包

仓库根目录应为 `kivy_android` 文件夹里的内容，包含 `main.py`、`geo_core.py`、`buildozer.spec` 和 `.github/workflows/build-apk.yml`。

### 1. 新建 GitHub 仓库

在 GitHub 点击 New repository，仓库名例如：

```text
kml-shp-converter
```

不要勾选初始化 README，保持空仓库。

### 2. 上传项目

在项目文件夹执行：

```powershell
cd C:\Users\gchy4\Documents\deepseek-harness\default-workspace\kivy_android

git init
git add .
git commit -m "Kivy Android KML SHP converter"
git branch -M main
git remote add origin https://github.com/你的用户名/kml-shp-converter.git
git push -u origin main
```

如果电脑没配置 Git 用户名：

```powershell
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
```

### 3. 触发打包

推送后打开仓库：

```text
Actions -> Build Android APK -> Run workflow -> Run workflow
```

也可以直接 push 代码触发；工作流只在修改 `main.py`、`geo_core.py`、`buildozer.spec` 时自动运行。

### 4. 下载 APK

构建完成后：

```text
Actions -> 最新的 Build Android APK -> 页面底部 Artifacts -> kml-shp-apk
```

下载得到 zip，解压后就是 APK。

首次构建通常需要 20-60 分钟；有缓存后再次构建会快很多。

### 5. 发版（可选）

如果要在 GitHub Releases 中附带 APK，把以下步骤加到 workflow 末尾：

```yaml
      - name: Create Release
        uses: softprops/action-gh-release@v2
        if: startsWith(github.ref, 'refs/tags/')
        with:
          files: bin/*.apk
```

然后打 tag 并推送：

```bash
git tag v1.0.0
git push origin v1.0.0
```
