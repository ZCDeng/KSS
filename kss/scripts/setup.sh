#!/bin/bash
# KSS setup script
set -e

echo "Setting up KSS environment..."

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 升级基础工具
pip install --upgrade pip setuptools wheel

# 安装 KSS 包（editable 模式）
pip install -e ./kss/

# 安装开发依赖（可选）
if [[ "${KSS_INSTALL_DEV:-0}" == "1" ]]; then
    echo "Installing dev dependencies..."
    pip install -e ./kss/[dev]
fi

# 安装深度学习依赖（可选）
if [[ "${KSS_INSTALL_DEEP:-0}" == "1" ]]; then
    echo "Installing deep-learning dependencies..."
    pip install -e ./kss/[deep]
fi

echo ""
echo "Setup complete. Activate with: source .venv/bin/activate"
echo "Run 'kss --help' to see available commands."
