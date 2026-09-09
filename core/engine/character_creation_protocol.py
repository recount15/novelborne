"""角色创建规程与标准模块。

基于用户最初创建的角色性格模型，提炼一套创建角色应包含的规程与标准，
包括自检流程，代码化以利用到角色生成器中。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------- 角色创建核心标准

# 基于用户最初创建的角色性格模型，提炼的核心标准
CHARACTER_CREATION_STANDARDS = {
    "identity": {
        "required_fields": ["name", "role"],
        "optional_fields": ["work", "archetype", "one_line", "gender", "source_medium", "source_region"],
        "validation_rules": {
            # name 允许：中文、英文字母数字、下划线连字符、空格、英文点（C.C.）、
            # 「·」中文间隔号（译名惯例）、全半角括号（原著名注记，如 爱姬斯（Aigis））
            "name": {"max_length": 40,
                     "pattern": r"^[\u4e00-\u9fa5a-zA-Z0-9_\-\s\.·（）()]+$"},
            "role": {"allowed_values": ["伙伴", "single_heroine", "multi_heroine", "主角", "反派", "女主"]},
            "gender": {"allowed_values": ["male", "female", "unknown"]},
        }
    },
    "personality": {
        "required_fields": ["desire", "fear", "voice", "unacceptable_actions"],
        "optional_fields": ["decision_principle", "voice_samples", "contrast"],
        "quality_criteria": {
            "desire": "必须具体、可驱动行为，不能是泛泛的'幸福'或'成功'",
            "fear": "必须与欲望形成张力，不能是泛泛的'失败'或'痛苦'",
            "voice": "必须包含具体语言特征，不能是泛泛的'温和'或'强势'",
            "unacceptable_actions": "必须具体、可验证，不能是泛泛的'坏事'",
        }
    },
    "abilities": {
        "required_fields": ["abilities"],
        "optional_fields": ["ability_limits", "ability_cost"],
        "quality_criteria": {
            "abilities": "必须与角色原型和背景一致，不能是泛泛的'聪明'或'强大'",
            "ability_limits": "必须明确代价、条件、冷却等限制",
        }
    },
    "relationships": {
        "required_fields": ["relationship_vector"],
        "optional_fields": ["knowledge_scope"],
        "quality_criteria": {
            "relationship_vector": "必须具体描述与关键角色的关系，不能是泛泛的'朋友'或'敌人'",
            "knowledge_scope": "必须明确角色知道什么、不知道什么",
        }
    },
    "background": {
        "required_fields": ["background"],
        "optional_fields": ["references"],
        "quality_criteria": {
            "background": "必须简洁但完整，包含关键经历和转折点",
            "references": "必须基于原著或官方设定，不能是主观臆测",
        }
    }
}

# ---------------------------------------------------------------- 角色创建流程

class CharacterCreationProtocol:
    """角色创建规程与标准"""
    
    def __init__(self):
        self.standards = CHARACTER_CREATION_STANDARDS
        self.validation_errors: List[str] = []
        self.quality_warnings: List[str] = []
    
    def validate_identity(self, identity: Mapping[str, Any]) -> bool:
        """验证身份信息"""
        self.validation_errors = []
        
        # 检查必填字段
        for field in self.standards["identity"]["required_fields"]:
            if not identity.get(field):
                self.validation_errors.append(f"身份信息缺少必填字段: {field}")
        
        # 验证字段格式
        for field, rules in self.standards["identity"]["validation_rules"].items():
            value = identity.get(field, "")
            if value:
                if "max_length" in rules and len(value) > rules["max_length"]:
                    self.validation_errors.append(f"字段 {field} 超过最大长度 {rules['max_length']}")
                if "pattern" in rules and not re.match(rules["pattern"], value):
                    self.validation_errors.append(f"字段 {field} 格式不正确")
                if "allowed_values" in rules and value not in rules["allowed_values"]:
                    self.validation_errors.append(f"字段 {field} 的值 {value} 不在允许范围内")
        
        return len(self.validation_errors) == 0
    
    def validate_personality(self, personality: Mapping[str, Any]) -> bool:
        """验证性格信息"""
        self.validation_errors = []
        
        # 检查必填字段
        for field in self.standards["personality"]["required_fields"]:
            if not personality.get(field):
                self.validation_errors.append(f"性格信息缺少必填字段: {field}")
        
        # 验证质量标准
        for field, criteria in self.standards["personality"]["quality_criteria"].items():
            value = personality.get(field, "")
            if value and len(value) < 10:
                self.quality_warnings.append(f"字段 {field} 内容过于简短，可能不符合质量标准: {criteria}")
        
        return len(self.validation_errors) == 0
    
    def validate_abilities(self, abilities_data: Mapping[str, Any]) -> bool:
        """验证能力信息"""
        self.validation_errors = []
        
        # 检查必填字段
        for field in self.standards["abilities"]["required_fields"]:
            if not abilities_data.get(field):
                self.validation_errors.append(f"能力信息缺少必填字段: {field}")
        
        # 验证能力列表
        abilities = abilities_data.get("abilities", [])
        if isinstance(abilities, list) and len(abilities) == 0:
            self.validation_errors.append("能力列表不能为空")
        
        return len(self.validation_errors) == 0
    
    def validate_relationships(self, relationships: Mapping[str, Any]) -> bool:
        """验证关系信息"""
        self.validation_errors = []
        
        # 检查必填字段
        for field in self.standards["relationships"]["required_fields"]:
            if not relationships.get(field):
                self.validation_errors.append(f"关系信息缺少必填字段: {field}")
        
        return len(self.validation_errors) == 0
    
    def validate_background(self, background_data: Mapping[str, Any]) -> bool:
        """验证背景信息"""
        self.validation_errors = []
        
        # 检查必填字段
        for field in self.standards["background"]["required_fields"]:
            if not background_data.get(field):
                self.validation_errors.append(f"背景信息缺少必填字段: {field}")
        
        return len(self.validation_errors) == 0
    
    def validate_character_card(self, character_data: Mapping[str, Any]) -> Tuple[bool, List[str], List[str]]:
        """验证完整的角色卡"""
        self.validation_errors = []
        self.quality_warnings = []
        
        # 验证各个部分
        identity_valid = self.validate_identity(character_data)
        personality_valid = self.validate_personality(character_data)
        abilities_valid = self.validate_abilities(character_data)
        relationships_valid = self.validate_relationships(character_data)
        background_valid = self.validate_background(character_data)
        
        # 检查整体一致性
        self._check_consistency(character_data)
        
        is_valid = (identity_valid and personality_valid and abilities_valid and 
                   relationships_valid and background_valid)
        
        return is_valid, self.validation_errors.copy(), self.quality_warnings.copy()
    
    def _check_consistency(self, character_data: Mapping[str, Any]) -> None:
        """检查角色卡各部分的一致性"""
        # 检查欲望与恐惧的张力
        desire = character_data.get("desire", "")
        fear = character_data.get("fear", "")
        if desire and fear and desire == fear:
            self.quality_warnings.append("欲望与恐惧相同，可能缺乏戏剧张力")
        
        # 检查能力与原型的匹配度
        archetype = character_data.get("archetype", "")
        abilities = character_data.get("abilities", [])
        if archetype and abilities:
            # 这里可以添加更复杂的匹配度检查
            pass
        
        # 检查语言风格与角色类型的匹配
        voice = character_data.get("voice", "")
        role = character_data.get("role", "")
        if voice and role:
            # 这里可以添加更复杂的匹配度检查
            pass
    
    def generate_character_creation_checklist(self) -> Dict[str, Any]:
        """生成角色创建检查清单"""
        checklist = {
            "identity": {
                "required": self.standards["identity"]["required_fields"],
                "optional": self.standards["identity"]["optional_fields"],
                "validation_rules": self.standards["identity"]["validation_rules"]
            },
            "personality": {
                "required": self.standards["personality"]["required_fields"],
                "optional": self.standards["personality"]["optional_fields"],
                "quality_criteria": self.standards["personality"]["quality_criteria"]
            },
            "abilities": {
                "required": self.standards["abilities"]["required_fields"],
                "optional": self.standards["abilities"]["optional_fields"],
                "quality_criteria": self.standards["abilities"]["quality_criteria"]
            },
            "relationships": {
                "required": self.standards["relationships"]["required_fields"],
                "optional": self.standards["relationships"]["optional_fields"],
                "quality_criteria": self.standards["relationships"]["quality_criteria"]
            },
            "background": {
                "required": self.standards["background"]["required_fields"],
                "optional": self.standards["background"]["optional_fields"],
                "quality_criteria": self.standards["background"]["quality_criteria"]
            }
        }
        return checklist
    
    def get_character_creation_guidelines(self) -> str:
        """获取角色创建指南"""
        guidelines = """
# 角色创建指南

## 1. 身份信息
- **必填字段**：name（角色名）、role（角色类型）
- **可选字段**：work（出处作品）、archetype（原型）、one_line（一句话概括）、gender（性别）、source_medium（来源媒介）、source_region（来源地区）
- **验证规则**：
  - name：最大40字符，只允许中文、字母、数字、下划线、连字符
  - role：必须是"伙伴"、"single_heroine"、"multi_heroine"、"主角"、"反派"、"女主"之一
  - gender：必须是"male"、"female"、"unknown"之一

## 2. 性格信息
- **必填字段**：desire（欲望）、fear（恐惧）、voice（语言风格）、unacceptable_actions（行为禁区）
- **可选字段**：decision_principle（决策原则）、voice_samples（台词样本）、contrast（表里反差）
- **质量标准**：
  - desire：必须具体、可驱动行为，不能是泛泛的"幸福"或"成功"
  - fear：必须与欲望形成张力，不能是泛泛的"失败"或"痛苦"
  - voice：必须包含具体语言特征，不能是泛泛的"温和"或"强势"
  - unacceptable_actions：必须具体、可验证，不能是泛泛的"坏事"

## 3. 能力信息
- **必填字段**：abilities（能力列表）
- **可选字段**：ability_limits（能力限制）、ability_cost（能力代价）
- **质量标准**：
  - abilities：必须与角色原型和背景一致，不能是泛泛的"聪明"或"强大"
  - ability_limits：必须明确代价、条件、冷却等限制

## 4. 关系信息
- **必填字段**：relationship_vector（关系向量）
- **可选字段**：knowledge_scope（知情范围）
- **质量标准**：
  - relationship_vector：必须具体描述与关键角色的关系，不能是泛泛的"朋友"或"敌人"
  - knowledge_scope：必须明确角色知道什么、不知道什么

## 5. 背景信息
- **必填字段**：background（背景故事）
- **可选字段**：references（原著判例）
- **质量标准**：
  - background：必须简洁但完整，包含关键经历和转折点
  - references：必须基于原著或官方设定，不能是主观臆测

## 6. 自检流程
1. 验证所有必填字段是否已填写
2. 检查字段格式是否符合验证规则
3. 评估内容质量是否符合质量标准
4. 检查各部分之间的一致性
5. 确保角色具有足够的戏剧张力和深度
"""
        return guidelines


# ---------------------------------------------------------------- 角色创建自检器

class CharacterSelfChecker:
    """角色创建自检器"""
    
    def __init__(self):
        self.protocol = CharacterCreationProtocol()
        self.check_results: Dict[str, Any] = {}
    
    def perform_self_check(self, character_data: Mapping[str, Any]) -> Dict[str, Any]:
        """执行自检"""
        self.check_results = {
            "overall_status": "pending",
            "validation_errors": [],
            "quality_warnings": [],
            "consistency_checks": {},
            "recommendations": []
        }
        
        # 执行验证
        is_valid, errors, warnings = self.protocol.validate_character_card(character_data)
        
        self.check_results["validation_errors"] = errors
        self.check_results["quality_warnings"] = warnings
        
        # 执行一致性检查
        self.check_results["consistency_checks"] = self._perform_consistency_checks(character_data)
        
        # 生成建议
        self.check_results["recommendations"] = self._generate_recommendations(character_data)
        
        # 确定整体状态
        if errors:
            self.check_results["overall_status"] = "failed"
        elif warnings:
            self.check_results["overall_status"] = "warning"
        else:
            self.check_results["overall_status"] = "passed"
        
        return self.check_results
    
    def _perform_consistency_checks(self, character_data: Mapping[str, Any]) -> Dict[str, bool]:
        """执行一致性检查"""
        checks = {}
        
        # 检查欲望与恐惧的张力
        desire = character_data.get("desire", "")
        fear = character_data.get("fear", "")
        checks["desire_fear_tension"] = bool(desire and fear and desire != fear)
        
        # 检查能力与原型的匹配度
        archetype = character_data.get("archetype", "")
        abilities = character_data.get("abilities", [])
        checks["archetype_ability_match"] = bool(archetype and abilities)
        
        # 检查语言风格与角色类型的匹配
        voice = character_data.get("voice", "")
        role = character_data.get("role", "")
        checks["voice_role_match"] = bool(voice and role)
        
        # 检查关系向量的具体性
        relationship_vector = character_data.get("relationship_vector", "")
        checks["relationship_specificity"] = bool(relationship_vector and len(relationship_vector) > 20)
        
        # 检查背景故事的完整性
        background = character_data.get("background", "")
        checks["background_completeness"] = bool(background and len(background) > 50)
        
        return checks
    
    def _generate_recommendations(self, character_data: Mapping[str, Any]) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 检查欲望的具体性
        desire = character_data.get("desire", "")
        if desire and len(desire) < 20:
            recommendations.append("建议将欲望描述得更具体，包含具体的目标和动机")
        
        # 检查恐惧的具体性
        fear = character_data.get("fear", "")
        if fear and len(fear) < 20:
            recommendations.append("建议将恐惧描述得更具体，包含具体的威胁和后果")
        
        # 检查语言风格的具体性
        voice = character_data.get("voice", "")
        if voice and len(voice) < 30:
            recommendations.append("建议将语言风格描述得更具体，包含具体的语言特征和习惯")
        
        # 检查行为禁区的具体性
        unacceptable_actions = character_data.get("unacceptable_actions", [])
        if isinstance(unacceptable_actions, list) and len(unacceptable_actions) < 2:
            recommendations.append("建议添加更多具体的行为禁区，增强角色的道德边界")
        
        # 检查能力列表的丰富度
        abilities = character_data.get("abilities", [])
        if isinstance(abilities, list) and len(abilities) < 3:
            recommendations.append("建议添加更多具体的能力，增强角色的实用性")
        
        # 检查关系向量的丰富度
        relationship_vector = character_data.get("relationship_vector", "")
        if relationship_vector and len(relationship_vector) < 50:
            recommendations.append("建议将关系向量描述得更详细，包含具体的关系动态")
        
        # 检查背景故事的丰富度
        background = character_data.get("background", "")
        if background and len(background) < 100:
            recommendations.append("建议将背景故事写得更详细，包含关键经历和转折点")
        
        return recommendations
    
    def get_self_check_report(self) -> str:
        """生成自检报告"""
        if not self.check_results:
            return "尚未执行自检"
        
        report_lines = []
        report_lines.append("# 角色创建自检报告")
        report_lines.append(f"\n## 整体状态: {self.check_results['overall_status'].upper()}")
        
        # 验证错误
        if self.check_results["validation_errors"]:
            report_lines.append("\n## 验证错误")
            for error in self.check_results["validation_errors"]:
                report_lines.append(f"- ❌ {error}")
        
        # 质量警告
        if self.check_results["quality_warnings"]:
            report_lines.append("\n## 质量警告")
            for warning in self.check_results["quality_warnings"]:
                report_lines.append(f"- ⚠️ {warning}")
        
        # 一致性检查
        if self.check_results["consistency_checks"]:
            report_lines.append("\n## 一致性检查")
            for check_name, passed in self.check_results["consistency_checks"].items():
                status = "✅" if passed else "❌"
                report_lines.append(f"- {status} {check_name}")
        
        # 改进建议
        if self.check_results["recommendations"]:
            report_lines.append("\n## 改进建议")
            for recommendation in self.check_results["recommendations"]:
                report_lines.append(f"- 💡 {recommendation}")
        
        return "\n".join(report_lines)


# ---------------------------------------------------------------- 角色创建器

class CharacterCreator:
    """角色创建器 - 集成规程与标准"""
    
    def __init__(self):
        self.protocol = CharacterCreationProtocol()
        self.self_checker = CharacterSelfChecker()
    
    def create_character(self, character_data: Mapping[str, Any]) -> Dict[str, Any]:
        """创建角色（带自检）"""
        # 执行自检
        check_results = self.self_checker.perform_self_check(character_data)
        
        # 如果有验证错误，返回错误
        if check_results["overall_status"] == "failed":
            return {
                "success": False,
                "errors": check_results["validation_errors"],
                "warnings": check_results["quality_warnings"],
                "recommendations": check_results["recommendations"]
            }
        
        # 如果有质量警告，返回警告但继续
        if check_results["overall_status"] == "warning":
            return {
                "success": True,
                "warnings": check_results["quality_warnings"],
                "recommendations": check_results["recommendations"],
                "character_data": character_data
            }
        
        # 通过自检，返回成功
        return {
            "success": True,
            "character_data": character_data,
            "check_results": check_results
        }
    
    def get_creation_guidelines(self) -> str:
        """获取创建指南"""
        return self.protocol.get_character_creation_guidelines()
    
    def get_creation_checklist(self) -> Dict[str, Any]:
        """获取创建检查清单"""
        return self.protocol.generate_character_creation_checklist()


# ---------------------------------------------------------------- 全局实例

character_creation_protocol = CharacterCreationProtocol()
character_self_checker = CharacterSelfChecker()
character_creator = CharacterCreator()


# ---------------------------------------------------------------- 为了向后兼容

def validate_character_card(character_data: Mapping[str, Any]) -> Tuple[bool, List[str], List[str]]:
    """验证角色卡（向后兼容接口）"""
    return character_creation_protocol.validate_character_card(character_data)


def get_character_creation_guidelines() -> str:
    """获取角色创建指南（向后兼容接口）"""
    return character_creation_protocol.get_character_creation_guidelines()


def perform_character_self_check(character_data: Mapping[str, Any]) -> Dict[str, Any]:
    """执行角色自检（向后兼容接口）"""
    return character_self_checker.perform_self_check(character_data)