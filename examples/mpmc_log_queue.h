#ifndef LOGMSG_QUEUE_H
#define LOGMSG_QUEUE_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#define LOGMSG_QUEUE_CAPACITY 512U
#define LOGMSG_QUEUE_MASK 0x1FFU
#define LOGMSG_QUEUE_ELEMENT_SIZE 128U
#define LOGMSG_QUEUE_BUFFER_SIZE 65536U

typedef struct {
    _Atomic(uint64_t) head;
    uint8_t _pad1[56];
    
    _Atomic(uint64_t) tail;
    uint8_t _pad2[56];
    
    uint8_t buffer[LOGMSG_QUEUE_BUFFER_SIZE];
} logmsg_queue_t;

static inline logmsg_queue_t *logmsg_queue_create(void) {
    logmsg_queue_t *rb = (logmsg_queue_t *)malloc(sizeof(logmsg_queue_t));
    if (!rb) return NULL;
    
    atomic_store_explicit(&rb->head, 0, memory_order_release);
    atomic_store_explicit(&rb->tail, 0, memory_order_release);
    memset(rb->buffer, 0, LOGMSG_QUEUE_BUFFER_SIZE);
    
    return rb;
}

static inline int logmsg_queue_push(logmsg_queue_t *rb, const void *data) {
    if (!rb || !data) return -1;
    
    uint64_t head, next_head;
    
    while (1) {
        head = atomic_load_explicit(&rb->head, memory_order_acquire);
        uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
        
        if (head - tail >= LOGMSG_QUEUE_CAPACITY) {
            return -1;
        }
        
        next_head = head + 1;
        
        if (atomic_compare_exchange_strong_explicit(
                &rb->head, &head, next_head,
                memory_order_release, memory_order_acquire)) {
            break;
        }
    }
    
    uint32_t index = (uint32_t)(head & LOGMSG_QUEUE_MASK);
    uint8_t *slot = rb->buffer + (index * LOGMSG_QUEUE_ELEMENT_SIZE);
    memcpy(slot, data, LOGMSG_QUEUE_ELEMENT_SIZE);
    
    return 0;
}

static inline int logmsg_queue_pop(logmsg_queue_t *rb, void *out) {
    if (!rb || !out) return -1;
    
    uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
    uint64_t head = atomic_load_explicit(&rb->head, memory_order_acquire);
    
    if (tail == head) {
        return -1;
    }
    
    uint32_t index = (uint32_t)(tail & LOGMSG_QUEUE_MASK);
    uint8_t *slot = rb->buffer + (index * LOGMSG_QUEUE_ELEMENT_SIZE);
    memcpy(out, slot, LOGMSG_QUEUE_ELEMENT_SIZE);
    
    atomic_store_explicit(&rb->tail, tail + 1, memory_order_release);
    
    return 0;
}

static inline void logmsg_queue_destroy(logmsg_queue_t *rb) {
    if (rb) {
        free(rb);
    }
}

#endif