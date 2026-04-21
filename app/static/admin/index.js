import { createAdminApi } from './api.js';
import { createOverviewModule } from './overview.js';
import { createDataSourcesModule } from './data-sources.js';
import { createExecutionEnginesModule } from './execution-engines.js';
import { createSemanticCatalogModule } from './semantic-catalog.js';
import { createAnalysisOpsModule } from './analysis-ops.js';
import { createRuntimeJobsModule } from './runtime-jobs.js';
import { createGovernanceModule } from './governance.js';
import { createObservabilityModule } from './observability.js';
import { createAdminShell } from './shell.js';

const shared = {
  esc: window.esc,
  closeModal: window.closeModal,
  openModal: window.openModal,
  toast: window.toast,
  renderEmptyState: window.renderEmptyState,
  renderLoadingState: window.renderLoadingState,
  renderErrorState: window.renderErrorState,
  renderStructuredError: window.renderStructuredError,
  renderJsonPanel: window.renderJsonPanel,
  renderDetailList: window.renderDetailList,
  renderResultsCount: window.renderResultsCount,
  renderAdminTableCard: window.renderAdminTableCard,
  renderAdminDetailCard: window.renderAdminDetailCard,
  renderAdminListDetailLayout: window.renderAdminListDetailLayout,
  normalizeApiError: window.normalizeApiError,
  pollAsync: window.pollAsync,
  formatKeyValueSummary: window.formatKeyValueSummary,
  buildMarivoUiUrl: window.buildMarivoUiUrl,
  buildUiSessionsUrl: window.buildUiSessionsUrl,
  buildUiStateUrl: window.buildUiStateUrl,
  buildUiContextUrl: window.buildUiContextUrl,
  buildUiRuntimeUrl: window.buildUiRuntimeUrl,
  buildUiJobsUrl: window.buildUiJobsUrl,
  adminUiDeepLinks: window.adminUiDeepLinks,
  openDangerConfirm: window.openDangerConfirm,
  statusBadge: window.statusBadge,
  fmtDate: window.fmtDate,
  initSidebar: window.initSidebar,
  setActiveTab: window.setActiveTab,
};

const adminApi = createAdminApi();
const ctx = {
  shared,
  adminApi,
  getCurrentRoute: () => null,
  applyAdminRoute: () => {},
  renderCurrentRoute: () => {},
};

const modules = {
  overview: createOverviewModule(ctx),
  dataSources: createDataSourcesModule(ctx),
  executionEngines: createExecutionEnginesModule(ctx),
  semanticCatalog: createSemanticCatalogModule(ctx),
  analysisOps: createAnalysisOpsModule(ctx),
  runtimeJobs: createRuntimeJobsModule(ctx),
  governance: createGovernanceModule(ctx),
  observability: createObservabilityModule(ctx),
};

ctx.modules = modules;

const shell = createAdminShell({ ...ctx, modules });

document.addEventListener('DOMContentLoaded', () => {
  shell.start();
});
