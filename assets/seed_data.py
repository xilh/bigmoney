"""
Seed the 5 default asset layers based on the asset allocation document.
Run: uv run python manage.py shell < assets/seed_data.py
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bigbill_project.settings')
django.setup()

from assets.models import AssetLayer

layers = [
    {
        'name': '第一层·安全垫',
        'description': '应急储备 + 机会资金。货币基金、银行理财(R1-R2)、大额存单。预期收益约2-2.5%。',
        'target_ratio': 12.5,
        'color': 'hsl(152, 65%, 50%)',
        'order': 1,
    },
    {
        'name': '第二层·债券核心',
        'description': '稳健收益，抵抗股市波动。中短债基金、纯债基金、可转债基金。预期年化3-5%。',
        'target_ratio': 22.5,
        'color': 'hsl(200, 80%, 55%)',
        'order': 2,
    },
    {
        'name': '第三层·股票核心',
        'description': '长期资本增值主引擎。沪深300ETF、红利股、优质价值成长股。子分类：宽基50%/红利30%/成长20%。',
        'target_ratio': 37.5,
        'color': 'hsl(35, 90%, 55%)',
        'order': 3,
    },
    {
        'name': '第四层·另类对冲',
        'description': '不相关收益来源、通胀对冲。黄金ETF(5-8%)、港股/QDII(5-7%)。',
        'target_ratio': 12.5,
        'color': 'hsl(45, 95%, 55%)',
        'order': 4,
    },
    {
        'name': '第五层·卫星机会',
        'description': '战术性机会、新兴主题、学习成长。行业ETF、打新、主题投资。严格控制仓位。',
        'target_ratio': 15.0,
        'color': 'hsl(300, 65%, 55%)',
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
