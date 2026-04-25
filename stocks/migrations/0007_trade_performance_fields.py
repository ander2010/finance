from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stocks', '0006_trade_volume_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='trade',
            name='paper_status',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('ACTIVE', 'Active'), ('CLOSED', 'Closed')],
                default='PENDING',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='trade',
            name='outcome',
            field=models.CharField(
                choices=[('WIN', 'Win'), ('LOSS', 'Loss'), ('BREAKEVEN', 'Breakeven')],
                blank=True,
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='trade',
            name='entry_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='exit_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='exit_price',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='pnl_dollars',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='pnl_percent',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='max_drawdown',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='max_profit',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trade',
            name='days_held',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
