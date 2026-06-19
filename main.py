import asyncio
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from .web import AuditWebController

# 检查并导入第三方依赖
try:
    from aip import AipContentCensor
    BAIDU_AIP_AVAILABLE = True
except ImportError:
    BAIDU_AIP_AVAILABLE = False
    AipContentCensor = None

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None


class AuditData:
    """审核数据封装类，用于传递审核相关的信息"""
    
    def __init__(self, event: AstrMessageEvent, audit_type: str, result: str, reason: str, 
                 group_name: str, user_nickname: str, user_id: str):
        self.event = event
        self.audit_type = audit_type
        self.result = result
        self.reason = reason
        self.group_name = group_name
        self.user_nickname = user_nickname
        self.user_id = user_id
        
    @property
    def group_id(self) -> Optional[str]:
        """从事件中获取群ID"""
        return self.event.get_group_id() if self.event else None


# 百度内容审核API集成类
class BaiduAuditAPI:
    """百度内容审核API封装类（使用官方SDK）"""
    
    def __init__(self, api_key: str, secret_key: str, strategy_id: str = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.strategy_id = strategy_id
        self._http_client = None
        
        # 初始化百度内容审核客户端
        if not BAIDU_AIP_AVAILABLE:
            logger.error("未安装baidu-aip包，请运行: pip install baidu-aip")
            self.client = None
            return
            
        try:
            # 百度SDK需要三个参数：appId, apiKey, secretKey
            # 我们没有appId，所以使用空字符串
            self.client = AipContentCensor("", api_key, secret_key)
            logger.info("百度内容审核客户端初始化成功")
        except Exception as e:
            logger.error(f"百度内容审核客户端初始化失败: {e}")
            self.client = None
    
    async def _get_http_client(self):
        """获取或创建HTTP客户端"""
        if self._http_client is None and HTTPX_AVAILABLE:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
            )
        return self._http_client
    
    async def close(self):
        """关闭HTTP客户端"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    async def text_censor(self, text: str) -> Dict:
        """文本内容审核"""
        if not self.client:
            return {"error": "百度内容审核客户端未初始化"}
        
        try:
            # 由于百度SDK是同步的，使用线程池执行异步操作
            def sync_text_censor():
                return self.client.textCensorUserDefined(text)
            
            with ThreadPoolExecutor() as executor:
                result = await asyncio.get_event_loop().run_in_executor(executor, sync_text_censor)
            
            return result
            
        except Exception as e:
            logger.error(f"文本审核API调用异常: {e}")
            return {"error": f"API调用异常: {e}"}
    
    async def image_censor(self, image_url: str) -> Dict:
        """图片内容审核"""
        if not self.client:
            return {"error": "百度内容审核客户端未初始化"}
        
        if not HTTPX_AVAILABLE:
            return {"error": "未安装httpx包，请运行: pip install httpx"}
        
        try:
            # 下载图片
            http_client = await self._get_http_client()
            if not http_client:
                return {"error": "HTTP客户端初始化失败"}
            
            response = await http_client.get(image_url)
            if response.status_code != 200:
                raise Exception(f"图片下载失败，状态码: {response.status_code}")
            
            image_data = response.content
            
            # 使用百度SDK进行图片审核，由于百度SDK是同步的，使用线程池执行异步操作
            def sync_image_censor():
                return self.client.imageCensorUserDefined(image_data)
            
            with ThreadPoolExecutor() as executor:
                result = await asyncio.get_event_loop().run_in_executor(executor, sync_image_censor)
            
            return result
            
        except Exception as e:
            logger.error(f"图片审核API调用异常: {e}")
            return {"error": f"API调用异常: {e}"}

# 审核结果解析器
class AuditResultParser:
    """审核结果解析器"""
    
    @staticmethod
    def parse_text_result(result: Dict) -> Tuple[str, str]:
        """解析文本审核结果"""
        if "error" in result:
            return "审核失败", result["error"]
        if "error_code" in result:
            return "审核失败", f"百度API错误({result.get('error_code','')}): {result.get('error_msg','未知错误')}"
        
        conclusion = result.get("conclusion", "")
        data = result.get("data", [])
        
        if conclusion == "合规":
            return "合规", ""
        elif conclusion == "不合规":
            reasons = []
            for item in data:
                if "msg" in item:
                    reasons.append(item["msg"])
            return "不合规", ", ".join(reasons)
        elif conclusion == "疑似":
            reasons = []
            for item in data:
                if "msg" in item:
                    reasons.append(item["msg"])
            reason_text = ", ".join(reasons) if reasons else "内容疑似违规，需要人工审核"
            return "疑似", reason_text
        else:
            import logging
            logging.getLogger("astrbot").error(f"【AIP审核调试】百度API返回的原始结果: {result}")
            return "审核失败", f"未知审核结果(conclusion={conclusion})"
    
    @staticmethod
    def parse_image_result(result: Dict) -> Tuple[str, str]:
        """解析图片审核结果"""
        if "error" in result:
            return "审核失败", result["error"]
        
        conclusion = result.get("conclusion", "")
        data = result.get("data", [])
        
        if conclusion == "合规":
            return "合规", ""
        elif conclusion == "不合规":
            reasons = []
            for item in data:
                if "msg" in item:
                    reasons.append(item["msg"])
                elif "type" in item:
                    reasons.append(item["type"])
            return "不合规", ", ".join(reasons)
        elif conclusion == "疑似":
            reasons = []
            for item in data:
                if "msg" in item:
                    reasons.append(item["msg"])
                elif "type" in item:
                    reasons.append(item["type"])
            reason_text = ", ".join(reasons) if reasons else "图片疑似违规，需要人工审核"
            return "疑似", reason_text
        else:
            return "审核失败", "未知审核结果"

# 违规记录管理器
class ViolationManager:
    """违规记录管理器"""
    
    def __init__(self):
        self.user_violations = defaultdict(list)  # 用户违规记录
        self.group_violations = defaultdict(list)  # 群组违规记录
    
    def add_violation(self, group_id: str, user_id: str, violation_type: str):
        """添加违规记录"""
        timestamp = time.time()
        
        # 用户违规记录
        self.user_violations[(group_id, user_id)].append(timestamp)
        
        # 群组违规记录
        self.group_violations[group_id].append(timestamp)
        
        # 清理过期记录
        self._cleanup_expired_records()
    
    def get_user_violation_count(self, group_id: str, user_id: str, time_window: int) -> int:
        """获取用户在指定时间窗口内的违规次数"""
        key = (group_id, user_id)
        if key not in self.user_violations:
            return 0
        
        cutoff_time = time.time() - time_window
        violations = [ts for ts in self.user_violations[key] if ts > cutoff_time]
        return len(violations)
    
    def get_group_violation_count(self, group_id: str, time_window: int) -> int:
        """获取群组在指定时间窗口内的违规次数"""
        if group_id not in self.group_violations:
            return 0
        
        cutoff_time = time.time() - time_window
        violations = [ts for ts in self.group_violations[group_id] if ts > cutoff_time]
        return len(violations)
    
    def _cleanup_expired_records(self):
        """清理过期记录（24小时前的记录）"""
        cutoff_time = time.time() - 86400  # 24小时
        
        # 清理用户记录
        for key in list(self.user_violations.keys()):
            self.user_violations[key] = [ts for ts in self.user_violations[key] if ts > cutoff_time]
            if not self.user_violations[key]:
                del self.user_violations[key]
        
        # 清理群组记录
        for group_id in list(self.group_violations.keys()):
            self.group_violations[group_id] = [ts for ts in self.group_violations[group_id] if ts > cutoff_time]
            if not self.group_violations[group_id]:
                del self.group_violations[group_id]

# 主插件类
@register(
    "astrbot_plugin_group_aip_review",
    "VanillaNahida",
    "基于百度内容审核API的群聊内容安全审查插件",
    "v1.2.0"
    )
class GroupAipReviewPlugin(Star):
    """基于百度内容审核API的群聊内容安全审查插件"""
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.baidu_api = None
        self.audit_parser = AuditResultParser()
        self.violation_manager = ViolationManager()
        
        # 初始化 Web 管理页面
        plugin_dir = Path(context.plugin_dir) if hasattr(context, "plugin_dir") else Path(__file__).parent
        self.web = AuditWebController(context, config, plugin_dir)
        self.web.register_routes()
        
        # 初始化百度API
        self._init_baidu_api()
    
    def _init_baidu_api(self):
        """初始化百度API"""
        baidu_config = self.config.get("baidu_audit", {})
        api_key = baidu_config.get("api_key")
        secret_key = baidu_config.get("secret_key")
        strategy_id = baidu_config.get("strategy_id")
        
        if not api_key or not secret_key:
            logger.warning("百度API配置不完整，插件将无法正常工作")
            return
        
        self.baidu_api = BaiduAuditAPI(api_key, secret_key, strategy_id)
        logger.info("百度内容审核API初始化完成")
    
    async def terminate(self):
        """插件卸载时关闭HTTP客户端"""
        if self.baidu_api:
            await self.baidu_api.close()
            logger.info("百度API HTTP客户端已关闭")
    
    def get_group_config(self, group_id: str) -> Dict:
        """获取群组配置"""
        disposal_config = self.config.get("disposal", {})
        default_config = disposal_config.get("default", {})
        group_custom = disposal_config.get("group_custom", [])
        
        # 获取群组自定义配置（template_list 格式）
        group_config = default_config.copy()
        rule_id = default_config.get("rule_id", "default")
        
        if group_custom and isinstance(group_custom, list):
            # 遍历所有群配置，查找匹配的群
            for custom_config in group_custom:
                if custom_config.get("group_id") == group_id:
                    # 更新配置（排除 group_id 和 __template_key）
                    for key, value in custom_config.items():
                        if key not in ["group_id", "__template_key"]:
                            group_config[key] = value
                    # 更新规则ID
                    if "rule_id" in custom_config:
                        rule_id = custom_config["rule_id"]
                    break
        
        return group_config
    
    def _is_group_enabled(self, group_id: str) -> bool:
        """检查群是否启用审核（基于群级别配置中的 enabled 字段）"""
        disposal_config = self.config.get("disposal", {})
        group_custom = disposal_config.get("group_custom", [])
        
        if group_custom and isinstance(group_custom, list):
            for custom_config in group_custom:
                if custom_config.get("group_id") == group_id:
                    return custom_config.get("enabled", True)
        
        # 没有群单独配置项，不启用
        return False
    
    async def _send_notification(self, group_id: str, message: str, group_name: str = None, user_nickname: str = None, user_id: str = None, event: AstrMessageEvent = None, audit_data: AuditData = None):
        """发送通知消息"""
        try:
            group_config = self.get_group_config(group_id)
            notify_group_id = group_config.get("notify_group_id")
            rule_id = group_config.get("rule_id", "default")
            
            if notify_group_id:
                # 如果提供了event和audit_data，使用合并转发消息
                if event and audit_data:
                    await self._send_forward_message(event, notify_group_id, message, audit_data)
                else:
                    # 否则使用普通消息通知
                    # 获取所有平台实例
                    platforms = self.context.platform_manager.get_insts()
                    
                    # 遍历所有平台，找到支持发送群消息的平台
                    for platform in platforms:
                        client = platform.get_client()
                        if hasattr(client, 'send_group_msg'):
                            # 在消息中添加群名称和用户昵称
                            notification_with_info = f"{message}\n群：{group_name}（{group_id}）\n用户：{user_nickname}（{user_id}）"
                            await client.send_group_msg(
                                group_id=notify_group_id,
                                message=notification_with_info
                            )
                            logger.info(f"发送通知到群 {notify_group_id}: {notification_with_info}")
                            break
        except Exception as e:
            logger.error(f"发送通知失败: {e}")
    
    async def _send_private_message(self, user_id: str, message: str):
        """发送私聊消息"""
        try:
            # 获取所有平台实例
            platforms = self.context.platform_manager.get_insts()
            
            # 遍历所有平台，找到支持发送私聊消息的平台
            for platform in platforms:
                client = platform.get_client()
                if hasattr(client, 'send_private_msg'):
                    await client.send_private_msg(
                        user_id=user_id,
                        message=message
                    )
                    logger.info(f"发送私聊消息给用户 {user_id}: {message}")
                    break
        except Exception as e:
            logger.error(f"发送私聊消息失败: {e}")
    
    async def _send_forward_message(self, event: AstrMessageEvent, notify_group_id: str, notification_msg: str, audit_data: AuditData):
        """发送合并转发消息"""
        try:
            # 获取所有平台实例
            platforms = self.context.platform_manager.get_insts()
            
            # 遍历所有平台，找到支持发送合并转发消息的平台
            for platform in platforms:
                client = platform.get_client()
                if hasattr(client, 'call_action'):
                    # 构建第一个node节点：Bot发送的通知消息
                    bot_node = {
                        "type": "node",
                        "data": {
                            "user_id": event.get_self_id(),
                            "nickname": "违规消息通知",
                            "id": "",
                            "content": [
                                {
                                    "type": "text",
                                    "data": {
                                        "text": f"{notification_msg}\n群：{audit_data.group_name}（{audit_data.group_id}）\n用户：{audit_data.user_nickname}（{audit_data.user_id}）"
                                    }
                                }
                            ]
                        }
                    }
                    
                    # 构建第二个node节点：用户发送的原始违规消息
                    user_content = []
                    
                    # 处理文本消息
                    if event.message_str:
                        user_content.append({
                            "type": "text",
                            "data": {
                                "text": event.message_str
                            }
                        })
                    
                    # 处理图片消息
                    for component in event.get_messages():
                        if isinstance(component, Image) and component.url:
                            user_content.append({
                                "type": "image",
                                "data": {
                                    "url": component.url
                                }
                            })
                    
                    # 如果没有内容，添加一个占位文本
                    if not user_content:
                        user_content.append({
                            "type": "text",
                            "data": {
                                "text": "[消息内容无法解析]"
                            }
                        })
                    
                    user_node = {
                        "type": "node",
                        "data": {
                            "user_id": audit_data.user_id,
                            "nickname": audit_data.user_nickname,
                            "id": "",
                            "content": user_content
                        }
                    }
                    
                    # 构建合并转发消息参数
                    forward_message = {
                        "group_id": notify_group_id,
                        "messages": [bot_node, user_node]
                    }
                    
                    # 发送合并转发消息
                    await client.api.call_action("send_forward_msg", **forward_message)
                    logger.info(f"发送合并转发消息到群 {notify_group_id} 成功")
                    break
        except Exception as e:
            logger.error(f"发送合并转发消息失败: {e}")
    
    async def _handle_audit_result(self, audit_data: AuditData):
        """处理审核结果"""
        group_id = audit_data.group_id
        
        if not group_id:  # 私聊消息
            return
        
        group_config = self.get_group_config(group_id)
        
        if audit_data.result == "合规":
            # 合规，不执行任何操作
            logger.debug(f"消息审核通过: {audit_data.audit_type} - 用户 {audit_data.user_id} 在群 {group_id}")
            
        elif audit_data.result == "不合规":
            # 不合规，立即撤回消息并记录违规
            await self._handle_non_compliant(audit_data, group_config)
            
        elif audit_data.result == "疑似":
            # 疑似违规，发送通知
            await self._handle_suspicious(audit_data, group_config)
            
        elif audit_data.result == "审核失败":
            # 审核失败，通知Bot主人
            await self._handle_audit_failure(audit_data.event, audit_data.audit_type, audit_data.reason, group_config)
    
    async def _handle_non_compliant(self, audit_data: AuditData, group_config: Dict):
        """处理不合规内容"""
        group_id = audit_data.group_id
        
        # 记录违规
        self.violation_manager.add_violation(group_id, audit_data.user_id, audit_data.audit_type)
        
        # 撤回消息
        await self._recall_message(audit_data.event)
        
        # 发送通知
        rule_id = group_config.get("rule_id", "default")
        notification_msg = f"⚠️ 检测到违规内容\n类型: {audit_data.audit_type}\n原因: {audit_data.reason}\n规则ID: {rule_id}"
        await self._send_notification(group_id, notification_msg, audit_data.group_name, audit_data.user_nickname, audit_data.user_id, audit_data.event, audit_data)
        
        # 检查是否需要禁言或踢人
        await self._check_and_apply_punishment(audit_data, group_config)
    
    async def _handle_suspicious(self, audit_data: AuditData, group_config: Dict):
        """处理疑似违规内容"""
        group_id = audit_data.group_id
        rule_id = group_config.get("rule_id", "default")
        
        # 发送通知给管理员核实
        notification_msg = f"❓ 检测到疑似违规内容\n类型: {audit_data.audit_type}\n原因: {audit_data.reason}\n规则ID: {rule_id}\n请管理员核实处理"
        await self._send_notification(group_id, notification_msg, audit_data.group_name, audit_data.user_nickname, audit_data.user_id, audit_data.event, audit_data)
    
    async def _handle_audit_failure(self, event: AstrMessageEvent, audit_type: str, reason: str, group_config: Dict):
        """处理审核失败"""
        admin_id = group_config.get("admin_id")
        if admin_id:
            # 通知管理员
            notification_msg = f"⚠️ 审核失败通知\n类型: {audit_type}\n原因: {reason}\n请检查API配置或网络连接"
            await self._send_private_message(admin_id, notification_msg)
            logger.warning(f"审核失败，已通知管理员: {reason}")
    
    async def _recall_message(self, event: AstrMessageEvent):
        """撤回消息"""
        try:
            message_id = event.message_obj.message_id
            await event.bot.delete_msg(message_id=message_id)
            logger.info(f"撤回消息成功: {message_id}")
        except Exception as e:
            logger.error(f"撤回消息失败: {e}")
    
    async def _check_and_apply_punishment(self, audit_data: AuditData, group_config: Dict):
        """检查并应用惩罚措施"""
        group_id = audit_data.group_id
        
        time_window = group_config.get("time_window", 300)
        
        # 检查单人违规次数
        user_violations = self.violation_manager.get_user_violation_count(group_id, audit_data.user_id, time_window)
        single_threshold = group_config.get("single_user_violation_threshold", 3)
        
        if single_threshold > 0 and user_violations >= single_threshold:
            # 禁言用户
            mute_duration = group_config.get("mute_duration", 86400)
            rule_id = group_config.get("rule_id", "default")
            await self._mute_user(audit_data.event, mute_duration)
            
            # 发送通知到通知群
            mute_time_str = self._format_mute_duration(mute_duration)
            notification_msg = f"⚠️ 用户违规禁言通知\n群ID: {group_id}\n用户ID: {audit_data.user_id}\n违规次数: {user_violations}次\n已禁言 {mute_time_str}，请管理员关注。\n规则ID: {rule_id}"
            await self._send_notification(group_id, notification_msg, audit_data.group_name, audit_data.user_nickname, audit_data.user_id)
            
            # 检查是否需要踢人
            kick_threshold = group_config.get("kick_user_threshold", 5)
            if kick_threshold > 0 and user_violations >= kick_threshold and group_config.get("kick_user", False):
                await self._kick_user(audit_data, group_config.get("is_kick_user_and_block", False))
        
        # 检查群组违规次数
        group_violations = self.violation_manager.get_group_violation_count(group_id, time_window)
        group_threshold = group_config.get("group_violation_threshold", 5)
        
        if group_threshold > 0 and group_violations >= group_threshold:
            # 开启全员禁言
            rule_id = group_config.get("rule_id", "default")
            await self._mute_all_members(audit_data.event)
            
            # 在通知群发送通知
            notification_msg = f"⚠️ 群内出现大量违规内容\n群ID: {group_id}\n违规次数: {group_violations}次\n已开启全员禁言，请管理员及时处理\n规则ID: {rule_id}"
            await self._send_notification(group_id, notification_msg, audit_data.group_name, audit_data.user_nickname, audit_data.user_id)
    
    
    def _format_mute_duration(self, duration: int) -> str:
        """格式化禁言时间显示"""
        if duration >= 3600:
            # 大于等于1小时，显示小时和分钟
            hours = duration // 3600
            remaining_seconds = duration % 3600
            minutes = remaining_seconds // 60
            if minutes > 0:
                return f"{hours} 小时 {minutes} 分钟"
            else:
                return f"{hours} 小时"
        elif duration >= 60:
            # 大于等于1分钟，显示分钟和秒
            minutes = duration // 60
            seconds = duration % 60
            if seconds > 0:
                return f"{minutes} 分钟 {seconds} 秒"
            else:
                return f"{minutes} 分钟"
        else:
            # 小于1分钟，显示秒
            return f"{duration} 秒"
    
    async def _mute_user(self, event: AstrMessageEvent, duration: int):
        """禁言用户"""
        try:
            await event.bot.set_group_ban(
                group_id=event.get_group_id(),
                user_id=event.get_sender_id(),
                duration=duration
            )
            logger.info(f"禁言用户成功: {event.get_sender_id()} {duration}秒")
        except Exception as e:
            logger.error(f"禁言用户失败: {e}")
    
    async def _kick_user(self, audit_data: AuditData, block: bool):
        """踢出用户"""
        try:
            group_id = audit_data.group_id
            
            await audit_data.event.bot.set_group_kick(
                group_id=group_id,
                user_id=audit_data.user_id,
                reject_add_request=block
            )
            logger.info(f"踢出用户成功: {audit_data.user_id}, 是否拉黑: {block}")
            
            # 发送通知
            rule_id = self.get_group_config(group_id).get("rule_id", "default")
            notification_msg = f"⚠️ 用户被踢出群聊\n群ID: {group_id}\n用户ID: {audit_data.user_id}\n是否拉黑: {'是' if block else '否'}\n规则ID: {rule_id}"
            await self._send_notification(group_id, notification_msg, audit_data.group_name, audit_data.user_nickname, audit_data.user_id)
            
        except Exception as e:
            logger.error(f"踢出用户失败: {e}")
    
    async def _mute_all_members(self, event: AstrMessageEvent):
        """全员禁言"""
        try:
            await event.bot.set_group_whole_ban(
                group_id=event.get_group_id(),
                enable=True
            )
            logger.info(f"开启全员禁言成功: 群 {event.get_group_id()}")
        except Exception as e:
            logger.error(f"全员禁言失败: {e}")
    
    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_message(self, event: AstrMessageEvent):
        """消息事件监听"""
        # 检查是否为群聊消息
        group_id = event.get_group_id()
        if not group_id:
            return
        
        # 检查群级别配置中是否启用审核
        if not self._is_group_enabled(group_id):
            return

        # 调试输出
        logger.debug(f"【百度内容审核】原始消息：{event.message_obj.raw_message}")
        
        # 检查用户权限（bot管理员、群主、管理员跳过审核）
        # 直接从原始消息的role字段检查权限
        if event.is_admin():
            logger.debug("用户为Bot管理员，跳过审核")
            return
        
        # 检查群权限（群主、管理员跳过审核）
        sender_role = event.message_obj.raw_message.get("sender", {}).get("role", "member") if event.message_obj.raw_message else "member"
        if sender_role in ["admin", "owner"]:
            logger.debug(f"用户为{sender_role}，跳过审核")
            return
        
        # 检查百度API是否可用
        if not self.baidu_api:
            logger.warning("百度API未初始化，跳过审核")
            return
        
        # 获取群名称和用户信息
        group_name = event.message_obj.raw_message.get("group_name", "未知群") if event.message_obj.raw_message else "未知群"
        user_nickname = event.message_obj.raw_message.get("sender", {}).get("nickname", "未知用户") if event.message_obj.raw_message and event.message_obj.raw_message.get("sender") else "未知用户"
        user_id = event.message_obj.raw_message.get("sender", {}).get("user_id", "未知用户号") if event.message_obj.raw_message and event.message_obj.raw_message.get("sender") else "未知用户号"
        
        # 获取群组配置
        group_config = self.get_group_config(group_id)
                
        # 提取消息内容
        message_text = event.message_str
        image_urls = []
        
        # 提取图片URL
        for component in event.get_messages():
            if isinstance(component, Image) and component.url:
                image_urls.append(component.url)
        
        # 文本审核
        enable_text_censor = group_config.get("enable_text_censor", self.config.get("enable_text_censor", True))
        if enable_text_censor and message_text:
            await self._audit_text(event, message_text, group_name, user_nickname, user_id)
        
        # 图片审核
        enable_image_censor = group_config.get("enable_image_censor", self.config.get("enable_image_censor", True))
        if enable_image_censor and image_urls:
            for image_url in image_urls:
                await self._audit_image(event, image_url, group_name, user_nickname, user_id)
    
    async def _audit_text(self, event: AstrMessageEvent, text: str, group_name: str, user_nickname: str, user_id: str):
        """文本审核"""
        try:
            result = await self.baidu_api.text_censor(text)
            audit_result, reason = self.audit_parser.parse_text_result(result)
            
            logger.info(f"文本审核结果: {audit_result} - 原因: {reason}")
            audit_data = AuditData(event, "文本", audit_result, reason, group_name, user_nickname, user_id)
            await self._handle_audit_result(audit_data)
            
        except Exception as e:
            logger.error(f"文本审核异常: {e}")
    
    async def _audit_image(self, event: AstrMessageEvent, image_url: str, group_name: str, user_nickname: str, user_id: str):
        """图片审核"""
        try:
            result = await self.baidu_api.image_censor(image_url)
            audit_result, reason = self.audit_parser.parse_image_result(result)
            
            logger.info(f"图片审核结果: {audit_result} - 原因: {reason}")
            audit_data = AuditData(event, "图片", audit_result, reason, group_name, user_nickname, user_id)
            await self._handle_audit_result(audit_data)
            
        except Exception as e:
            logger.error(f"图片审核异常: {e}")
    
    async def initialize(self):
        """插件初始化"""
        logger.info("群聊内容安全审查插件初始化完成")
    
    async def terminate(self):
        """插件销毁"""
        logger.info("群聊内容安全审查插件已卸载")

    # 命令：开启内容审核
    @filter.command("开启内容审核")
    async def enable_audit(self, event: AstrMessageEvent):
        """开启当前群的内容审核"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此命令")
            return
        
        # 检查机器人权限
        try:
            bot_info = await event.bot.api.call_action("get_group_member_info", group_id=group_id, user_id=int(event.get_self_id()))
            bot_role = bot_info.get("role")
            if bot_role not in ["admin", "owner"]:
                yield event.plain_result("bot权限不足，需要管理员权限")
                return
        except Exception as e:
            logger.error(f"[群消息内容安全审核插件] 检查机器人权限失败: {e}")
            yield event.plain_result("bot权限不足，需要管理员权限")
            return
        
        # 检查用户权限（bot管理员、群主、管理员跳过审核）
        if event.is_admin():
            logger.debug("用户为Bot管理员，跳过审核")
        else:
            # 检查群权限（群主、管理员跳过审核）
            sender_role = event.message_obj.raw_message.get("sender", {}).get("role", "member") if event.message_obj.raw_message else "member"
            if sender_role not in ["admin", "owner"]:
                yield event.plain_result("您没有权限使用此命令，需要管理员或群主权限")
                return

        # 获取当前启用的群列表
        if self._is_group_enabled(group_id):
            yield event.plain_result(f"本群({group_id})的内容审核已经开启")
            return

        # 在群配置中启用审核
        disposal_config = self.config.get("disposal", {})
        group_custom = disposal_config.get("group_custom", [])
        has_group_config = False
        
        if group_custom and isinstance(group_custom, list):
            for custom_config in group_custom:
                if custom_config.get("group_id") == group_id:
                    custom_config["enabled"] = True
                    has_group_config = True
                    break
        
        if has_group_config:
            disposal_config["group_custom"] = group_custom
            self.config["disposal"] = disposal_config
            self.config.save_config()
        else:
            # 创建新的群配置项
            new_config = {
                "group_id": group_id,
                "enabled": True,
                "__template_key": "default_group_config"
            }
            group_custom.append(new_config)
            disposal_config["group_custom"] = group_custom
            self.config["disposal"] = disposal_config
            self.config.save_config()

        # 构建回复消息
        reply_msg = f"✅ 已成功开启本群({group_id})的内容审核"
        if not has_group_config:
            reply_msg += "\n\n⚠️ 注意：当前不存在群单独配置项，将使用默认全局配置项，建议前往WebUI添加群单独配置项。"

        yield event.plain_result(reply_msg)

    # 命令：关闭内容审核
    @filter.command("关闭内容审核")
    async def disable_audit(self, event: AstrMessageEvent):
        """关闭当前群的内容审核"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此命令")
            return
        
        # 检查机器人权限
        try:
            bot_info = await event.bot.api.call_action("get_group_member_info", group_id=group_id, user_id=int(event.get_self_id()))
            bot_role = bot_info.get("role")
            if bot_role not in ["admin", "owner"]:
                yield event.plain_result("bot权限不足，需要管理员权限")
                return
        except Exception as e:
            logger.error(f"[群消息内容安全审核插件] 检查机器人权限失败: {e}")
            yield event.plain_result("bot权限不足，需要管理员权限")
            return
        
        # 检查用户权限（bot管理员、群主、管理员跳过审核）
        if event.is_admin():
            logger.debug("用户为Bot管理员，跳过审核")
        else:
            # 检查群权限（群主、管理员跳过审核）
            sender_role = event.message_obj.raw_message.get("sender", {}).get("role", "member") if event.message_obj.raw_message else "member"
            if sender_role not in ["admin", "owner"]:
                yield event.plain_result("您没有权限使用此命令，需要管理员或群主权限")
                return

        # 获取当前启用的群列表
        if not self._is_group_enabled(group_id):
            yield event.plain_result(f"本群({group_id})的内容审核已经关闭")
            return

        # 在群配置中禁用审核
        disposal_config = self.config.get("disposal", {})
        group_custom = disposal_config.get("group_custom", [])
        
        if group_custom and isinstance(group_custom, list):
            for custom_config in group_custom:
                if custom_config.get("group_id") == group_id:
                    custom_config["enabled"] = False
                    break
        
        disposal_config["group_custom"] = group_custom
        self.config["disposal"] = disposal_config
        self.config.save_config()

        yield event.plain_result(f"✅ 已成功关闭本群({group_id})的内容审核")

    # 命令：查看审核配置
    @filter.command("查看审核配置")
    async def check_audit_config(self, event: AstrMessageEvent):
        """查看当前群的审核配置"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此命令")
            return
        
        # 检查机器人权限
        try:
            bot_info = await event.bot.api.call_action("get_group_member_info", group_id=group_id, user_id=int(event.get_self_id()))
            bot_role = bot_info.get("role")
            if bot_role not in ["admin", "owner"]:
                yield event.plain_result("bot权限不足，需要管理员权限")
                return
        except Exception as e:
            logger.error(f"[群消息内容安全审核插件] 检查机器人权限失败: {e}")
            yield event.plain_result("bot权限不足，需要管理员权限")
            return
        
        # 检查用户权限（bot管理员、群主、管理员跳过审核）
        if event.is_admin():
            logger.debug("用户为Bot管理员，跳过审核")
        else:
            # 检查群权限（群主、管理员跳过审核）
            sender_role = event.message_obj.raw_message.get("sender", {}).get("role", "member") if event.message_obj.raw_message else "member"
            if sender_role not in ["admin", "owner"]:
                yield event.plain_result("您没有权限使用此命令，需要管理员或群主权限")
                return

        # 获取群配置
        group_config = self.get_group_config(group_id)
        
        # 检查是否启用
        is_enabled = self._is_group_enabled(group_id)

        # 检查是否存在群单独配置项
        disposal_config = self.config.get("disposal", {})
        group_custom = disposal_config.get("group_custom", [])
        has_group_config = False
        
        if group_custom and isinstance(group_custom, list):
            for custom_config in group_custom:
                if custom_config.get("group_id") == group_id:
                    has_group_config = True
                    break

        # 构建配置信息
        config_info = "📋 群聊内容审核配置\n"
        config_info += f"群号：{group_id}\n"
        config_info += f"状态：{'✅已开启' if is_enabled else '❌已关闭'}\n\n"
        
        config_info += "当前使用的配置：\n"
        config_info += f"- 配置类型：{'群单独配置' if has_group_config else '全局默认配置'}\n"
        # 审核开关配置
        enable_text_censor = group_config.get("enable_text_censor", self.config.get("enable_text_censor", True))
        enable_image_censor = group_config.get("enable_image_censor", self.config.get("enable_image_censor", True))
        # 提示消息        
        config_info += f"- 文本审核：{'✅启用' if enable_text_censor else '❌禁用'}\n"
        config_info += f"- 图片审核：{'✅启用' if enable_image_censor else '❌禁用'}\n"
        config_info += f"- 审核规则ID：{group_config.get('rule_id', 'default')}\n"
        config_info += f"- 禁言阈值：{group_config.get('single_user_violation_threshold', 3)}次违规后禁言\n"
        config_info += f"- 禁言时长：{self._format_mute_duration(group_config.get('mute_duration', 3600))}\n"
        config_info += f"- 是否启用踢人：{'✅是' if group_config.get('kick_user', False) else '❌否'}\n"
        config_info += f"- 踢人阈值：{group_config.get('kick_user_threshold', 5)}次违规后踢出\n"
        config_info += f"- 是否踢出并拉黑用户：{'✅是' if group_config.get('is_kick_user_and_block', False) else '❌否'}\n"
        
        if not has_group_config:
            config_info += "\n⚠️ 注意：当前使用的是默认全局配置，建议前往WebUI添加群单独配置项以获得更精细的控制。"

        yield event.plain_result(config_info)
