<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="initialLoading" class="loading">{{ t('common.loading') }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
          <span class="budget-value">{{ formatMoney(budget) }}</span>
        </div>
        <input
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          :max="sliderMax"
          :step="BUDGET_STEP"
          :aria-label="t('restocking.budget')"
        />
        <div class="slider-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ formatMoney(sliderMax) }}</span>
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation?.total_cost ?? 0) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation?.remaining_budget ?? 0) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.leadTime') }}</div>
          <div class="stat-value">{{ t('restocking.days', { count: recommendation?.lead_time_days ?? 0 }) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemCount') }}</div>
          <div class="stat-value">{{ items.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ items.length }})</h3>
        </div>

        <div v-if="items.length === 0" class="empty-state">{{ t('restocking.noItems') }}</div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>{{ translateProductName(item.name) }}</td>
                <td><span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span></td>
                <td class="num">
                  {{ item.quantity.toLocaleString() }}
                  <template v-if="item.partial">
                    / {{ item.forecasted_demand.toLocaleString() }}
                    <span class="badge warning">{{ t('restocking.partial') }}</span>
                  </template>
                </td>
                <td class="num">{{ formatUnitCost(item.unit_cost) }}</td>
                <td class="num"><strong>{{ formatMoney(item.line_total) }}</strong></td>
                <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="order-actions">
          <div v-if="submitError" class="error">{{ submitError }}</div>
          <div v-if="lastOrder" class="success-message">
            {{ t('restocking.orderPlaced', { orderNumber: lastOrder.order_number }) }}
            <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
          </div>
          <button class="place-order-btn" :disabled="!canPlaceOrder" @click="placeOrder">
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

// Slider granularity in USD. The slider max is rounded UP to a multiple of
// this so a full restock (max_budget) is always reachable.
const BUDGET_STEP = 500
// Wait this long after the slider stops moving before asking the server.
const DEBOUNCE_MS = 200

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const initialLoading = ref(true)
    const error = ref(null)
    const budget = ref(0)
    const maxBudget = ref(0)
    const recommendation = ref(null)
    const submitting = ref(false)
    const submitError = ref(null)
    const lastOrder = ref(null)

    let debounceTimer = null
    // Incremented per request. Responses from older requests are dropped so a
    // slow response cannot overwrite a newer one while the slider moves.
    let requestSeq = 0

    const sliderMax = computed(() => Math.ceil(maxBudget.value / BUDGET_STEP) * BUDGET_STEP)
    const items = computed(() => recommendation.value?.items ?? [])
    const canPlaceOrder = computed(() => items.value.length > 0 && !submitting.value)

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)
    const formatUnitCost = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

    const fetchRecommendations = async (value) => {
      const seq = ++requestSeq
      try {
        const data = await api.getRestockRecommendations(value)
        if (seq !== requestSeq) return
        recommendation.value = data
        error.value = null
      } catch (err) {
        if (seq !== requestSeq) return
        error.value = `${t('restocking.loadError')}: ${err.message}`
        console.error('Restock recommendations error:', err)
      }
    }

    const init = async () => {
      try {
        // budget=0 is a cheap call whose max_budget sizes the slider.
        const probe = await api.getRestockRecommendations(0)
        maxBudget.value = probe.max_budget
        budget.value = Math.round(sliderMax.value / 2 / BUDGET_STEP) * BUDGET_STEP
        await fetchRecommendations(budget.value)
      } catch (err) {
        error.value = `${t('restocking.loadError')}: ${err.message}`
        console.error('Restock init error:', err)
      } finally {
        initialLoading.value = false
      }
    }

    watch(budget, (value) => {
      // init() sets the starting budget and fetches directly; skip that change.
      if (initialLoading.value) return
      submitError.value = null
      lastOrder.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => fetchRecommendations(value), DEBOUNCE_MS)
    })

    const placeOrder = async () => {
      if (!canPlaceOrder.value) return
      submitting.value = true
      submitError.value = null
      try {
        lastOrder.value = await api.createRestockOrder({
          // Send the budget the displayed items were computed for, not the
          // live slider value: a debounced refetch may still be pending.
          budget: recommendation.value.budget,
          items: items.value.map(item => ({ sku: item.sku, quantity: item.quantity }))
        })
      } catch (err) {
        // Prefer the server's 400 message; 422 details are arrays, so fall back.
        const detail = err.response?.data?.detail
        submitError.value = typeof detail === 'string' ? detail : err.message
        console.error('Place restock order error:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(init)
    onBeforeUnmount(() => clearTimeout(debounceTimer))

    return {
      t,
      BUDGET_STEP,
      initialLoading,
      error,
      budget,
      sliderMax,
      recommendation,
      items,
      submitting,
      submitError,
      lastOrder,
      canPlaceOrder,
      formatMoney,
      formatUnitCost,
      translateProductName,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}

.budget-slider {
  width: 100%;
  accent-color: #0f172a;
  cursor: pointer;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.25rem;
  font-size: 0.813rem;
  color: #64748b;
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
}

.order-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid #e2e8f0;
}

.order-actions .error {
  margin: 0;
  flex: 1;
}

.success-message {
  color: #15803d;
  font-size: 0.875rem;
}

.success-message a {
  margin-left: 0.5rem;
  color: #2563eb;
  font-weight: 600;
}

.place-order-btn {
  background: #0f172a;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 0.625rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
</style>
