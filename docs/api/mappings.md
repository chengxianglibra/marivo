# Mappings

`/mappings` is not implemented in the current HTTP API.

Current Marivo execution is dataset-native:

- `POST /datasources` registers the live datasource connection.
- OSI datasets carry `dataset.custom_extensions[].data.datasource_id`.
- OSI datasets carry the datasource-local relation name in `dataset.source`.
- `field.expression.dialects[]` carries physical column/expression grounding.
- `POST /routing/resolve` can inspect the datasource route selected for table
  names.

Do not call `POST /mappings`, `GET /mappings`, `PUT /mappings/{id}`, or
`DELETE /mappings/{id}` against the current service; these paths are not
mounted by the active FastAPI router.
