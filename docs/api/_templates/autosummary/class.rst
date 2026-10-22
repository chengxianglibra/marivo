{{ objname | escape | underline }}

.. currentmodule:: {{ module }}

.. autoclass:: {{ objname }}
   :members:
   :show-inheritance:
{% if fullname == 'marivo.analysis.TimeScope' %}   :exclude-members: model_dump
{% endif %}
{% if fullname == 'marivo.analysis.FrameMethodFacts' %}

.. automethod:: {{ objname }}.show
{% endif %}
