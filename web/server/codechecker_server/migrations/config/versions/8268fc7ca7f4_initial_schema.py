"""
Initial schema

Revision ID: 8268fc7ca7f4
Revises:     <None>
Create Date: 2017-09-18 20:57:11.098460
"""

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '8268fc7ca7f4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'db_version',
        sa.Column('major', sa.Integer(), nullable=False),
        sa.Column('minor', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('major', 'minor', name=op.f('pk_db_version'))
    )

    op.create_table(
        'permissions_system',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.Enum('SUPERUSER', name='sys_perms'),
                  nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_group', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions_system'))
    )

    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('endpoint', sa.String(), nullable=False),
        sa.Column('connection', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_products')),
        sa.UniqueConstraint('endpoint', name=op.f('uq_products_endpoint'))
    )

    op.create_table(
        'permissions_product',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.Enum('PRODUCT_ADMIN',
                                        'PRODUCT_ACCESS',
                                        'PRODUCT_STORE',
                                        name='product_perms'),
                  nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_group', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['product_id'], ['products.id'],
            name=op.f('fk_permissions_product_product_id_products'),
            ondelete='CASCADE', initially="IMMEDIATE", deferrable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions_product'))
    )


def downgrade():
    op.drop_table('permissions_product')
    op.drop_table('products')
    op.drop_table('permissions_system')
    op.drop_table('db_version')
