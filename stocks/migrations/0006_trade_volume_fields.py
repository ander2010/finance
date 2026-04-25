from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stocks', '0005_trade_resistance_level_trade_score_breakdown_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='trade',
            name='current_volume',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='avg_volume_20',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='relative_volume',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='volume_quality',
            field=models.CharField(blank=True, max_length=10),
        ),
    ]
