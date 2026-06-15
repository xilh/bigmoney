"""
Seed the 5 default asset layers based on the asset allocation document.
Run: uv run python manage.py shell < assets/seed_data.py
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bigbill_project.settings')
django.setup()

from assets.models import AssetLayer

# 五层架构与目标比例对齐《资产配置方案 v3.4》§2.1。
# 区间：T1 15-20 / T2 15-20 / T3 30-35 / T4 8-12 / T5 10-15。
# 采用用户确认的 100% 落点：20/20/35/12/13。
layers = [
    {
        'name': '第一层·安全垫',
        'description': '家庭应急 + 干火药。货币基金、短债、银行理财(R1-R2)。区间 15-20%。',
        'target_ratio': 20.0,
        'color': 'hsl(152, 65%, 50%)',
        'order': 1,
    },
    {
        'name': '第二层·固收增强',
        'description': '稳定收益底仓。纯债基金、固收+理财。区间 15-20%。',
        'target_ratio': 20.0,
        'color': 'hsl(200, 80%, 55%)',
        'order': 2,
    },
    {
        'name': '第三层·权益核心',
        'description': '长期增值主力。中证A500ETF、红利低波ETF、精选个股。子分类：宽基50-60%/红利25-35%/精选个股10-20%（单只≤总资产3%）。区间 30-35%。',
        'target_ratio': 35.0,
        'color': 'hsl(35, 90%, 55%)',
        'order': 3,
    },
    {
        'name': '第四层·另类对冲',
        'description': '抗通胀 + 地缘对冲。华安黄金ETF、商品基金。黄金按双指标规则触发建仓。区间 8-12%。',
        'target_ratio': 12.0,
        'color': 'hsl(45, 95%, 55%)',
        'order': 4,
    },
    {
        'name': '第五层·全球分散',
        'description': '跨市场分散。港股通ETF、QDII基金。子分类：港股通30-40%/QDII50-60%/主题≤10%。QDII内部美股宽基优先。区间 10-15%。',
        'target_ratio': 13.0,
        'color': 'hsl(265, 70%, 60%)',
        'order': 5,
    },
]

for layer_data in layers:
    obj, created = AssetLayer.objects.update_or_create(
        order=layer_data['order'],
        defaults=layer_data,
    )
    status = '创建' if created else '更新'
    print(f'{status}: {obj.name} ({obj.target_ratio}%)')

print('\n✅ 五层资产配置初始化完成!')
