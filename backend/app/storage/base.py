"""
Base Storage Class / 存储基类
Common utilities for file operations
文件操作的通用工具
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
import aiofiles


class BaseStorage:
    """Base storage class with common file operations / 带通用文件操作的存储基类"""
    
    def __init__(self, data_dir: str = "../data"):
        """
        Initialize storage
        
        Args:
            data_dir: Root data directory / 数据根目录
        """
        self.data_dir = Path(data_dir)
        self.encoding = "utf-8"
    
    def get_project_path(self, project_id: str) -> Path:
        """Get project directory path / 获取项目目录路径"""
        return self.data_dir / project_id
    
    def ensure_dir(self, path: Path) -> None:
        """Ensure directory exists / 确保目录存在"""
        path.mkdir(parents=True, exist_ok=True)
    
    async def read_yaml(self, file_path: Path) -> Dict[str, Any]:
        """
        Read YAML file asynchronously / 异步读取 YAML 文件
        
        Args:
            file_path: Path to YAML file / YAML 文件路径
            
        Returns:
            Parsed YAML content / 解析后的内容
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        async with aiofiles.open(file_path, 'r', encoding=self.encoding) as f:
            content = await f.read()
            return yaml.safe_load(content)
    
    async def write_yaml(self, file_path: Path, data: Dict[str, Any]) -> None:
        """
        Write YAML file asynchronously / 异步写入 YAML 文件
        
        Args:
            file_path: Path to YAML file / YAML 文件路径
            data: Data to write / 要写入的数据
        """
        self.ensure_dir(file_path.parent)
        
        async with aiofiles.open(file_path, 'w', encoding=self.encoding) as f:
            yaml_content = yaml.dump(data, allow_unicode=True, sort_keys=False)
            await f.write(yaml_content)
    
    async def read_jsonl(self, file_path: Path) -> list:
        """
        Read JSONL file / 读取 JSONL 文件
        
        Args:
            file_path: Path to JSONL file / JSONL 文件路径
            
        Returns:
            List of parsed JSON objects / JSON 对象列表
        """
        if not file_path.exists():
            return []
        
        items = []
        async with aiofiles.open(file_path, 'r', encoding=self.encoding) as f:
            async for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items
    
    async def append_jsonl(self, file_path: Path, item: Dict[str, Any]) -> None:
        """
        Append item to JSONL file / 追加条目到 JSONL 文件
        
        Args:
            file_path: Path to JSONL file / JSONL 文件路径
            item: Item to append / 要追加的条目
        """
        self.ensure_dir(file_path.parent)
        
        async with aiofiles.open(file_path, 'a', encoding=self.encoding) as f:
            await f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    async def read_text(self, file_path: Path) -> str:
        """
        Read text file / 读取文本文件
        
        Args:
            file_path: Path to text file / 文本文件路径
            
        Returns:
            File content / 文件内容
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        async with aiofiles.open(file_path, 'r', encoding=self.encoding) as f:
            return await f.read()
    
    async def write_text(self, file_path: Path, content: str) -> None:
        """
        Write text file / 写入文本文件
        
        Args:
            file_path: Path to text file / 文本文件路径
            content: Content to write / 要写入的内容
        """
        self.ensure_dir(file_path.parent)
        
        async with aiofiles.open(file_path, 'w', encoding=self.encoding) as f:
            await f.write(content)
