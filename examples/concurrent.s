	.file	"tmpe13tg1go.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	lockfree_arena_create
	.type	lockfree_arena_create, @function
lockfree_arena_create:
	endbr64
	sub	rsp, 8
	mov	esi, 4160
	mov	edi, 64
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L1
	lea	rdx, 87[rax]
	and	rdx, -64
	mov	QWORD PTR [rax], rdx
	xor	edx, edx
	xchg	rdx, QWORD PTR 8[rax]
	mov	QWORD PTR 16[rax], 4080
.L1:
	add	rsp, 8
	ret
	.size	lockfree_arena_create, .-lockfree_arena_create
	.p2align 4
	.globl	lockfree_arena_alloc
	.type	lockfree_arena_alloc, @function
lockfree_arena_alloc:
	endbr64
	lea	rax, -1[rsi]
	lea	rcx, 8[rdi]
	cmp	rax, 255
	ja	.L11
.L9:
	mov	rax, QWORD PTR [rcx]
	lea	rdx, 63[rax]
	and	rdx, -64
	lea	r8, [rsi+rdx]
	cmp	QWORD PTR 16[rdi], r8
	jb	.L11
	lock cmpxchg	QWORD PTR [rcx], r8
	jne	.L9
	mov	rax, QWORD PTR [rdi]
	add	rax, rdx
	ret
	.p2align 4,,10
	.p2align 3
.L11:
	xor	eax, eax
	ret
	.size	lockfree_arena_alloc, .-lockfree_arena_alloc
	.p2align 4
	.globl	lockfree_arena_reset
	.type	lockfree_arena_reset, @function
lockfree_arena_reset:
	endbr64
	mov	QWORD PTR 8[rdi], 0
	ret
	.size	lockfree_arena_reset, .-lockfree_arena_reset
	.p2align 4
	.globl	lockfree_arena_destroy
	.type	lockfree_arena_destroy, @function
lockfree_arena_destroy:
	endbr64
	test	rdi, rdi
	je	.L17
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L17:
	ret
	.size	lockfree_arena_destroy, .-lockfree_arena_destroy
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	sub	rsp, 8
	mov	esi, 4160
	mov	edi, 64
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L19
	lea	rdx, 87[rax]
	and	rdx, -64
	mov	QWORD PTR [rax], rdx
	xor	edx, edx
	xchg	rdx, QWORD PTR 8[rax]
	mov	QWORD PTR 16[rax], 4080
.L19:
	add	rsp, 8
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_alloc
	.type	_force_alloc, @function
_force_alloc:
	endbr64
	lea	rax, -1[rsi]
	lea	rcx, 8[rdi]
	cmp	rax, 255
	ja	.L28
.L26:
	mov	rax, QWORD PTR [rcx]
	lea	rdx, 63[rax]
	and	rdx, -64
	lea	r8, [rsi+rdx]
	cmp	QWORD PTR 16[rdi], r8
	jb	.L28
	lock cmpxchg	QWORD PTR [rcx], r8
	jne	.L26
	mov	rax, QWORD PTR [rdi]
	add	rax, rdx
	ret
	.p2align 4,,10
	.p2align 3
.L28:
	xor	eax, eax
	ret
	.size	_force_alloc, .-_force_alloc
	.p2align 4
	.globl	_force_reset
	.type	_force_reset, @function
_force_reset:
	endbr64
	mov	QWORD PTR 8[rdi], 0
	ret
	.size	_force_reset, .-_force_reset
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L34
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L34:
	ret
	.size	_force_destroy, .-_force_destroy
	.ident	"GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
	.section	.note.GNU-stack,"",@progbits
	.section	.note.gnu.property,"a"
	.align 8
	.long	1f - 0f
	.long	4f - 1f
	.long	5
0:
	.string	"GNU"
1:
	.align 8
	.long	0xc0000002
	.long	3f - 2f
2:
	.long	0x3
3:
	.align 8
4:
