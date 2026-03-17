	.file	"tmpc68aj8vh.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	key_material_arena_create
	.type	key_material_arena_create, @function
key_material_arena_create:
	endbr64
	sub	rsp, 8
	mov	esi, 544
	mov	edi, 16
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L1
	lea	rdx, 47[rax]
	movdqa	xmm0, XMMWORD PTR .LC0[rip]
	mov	QWORD PTR 24[rax], 0
	and	rdx, -16
	mov	QWORD PTR [rax], rdx
	movups	XMMWORD PTR 8[rax], xmm0
.L1:
	add	rsp, 8
	ret
	.size	key_material_arena_create, .-key_material_arena_create
	.p2align 4
	.globl	key_material_arena_alloc
	.type	key_material_arena_alloc, @function
key_material_arena_alloc:
	endbr64
	lea	rax, -1[rsi]
	cmp	rax, 495
	ja	.L10
	mov	rax, QWORD PTR 8[rdi]
	mov	rdx, rsi
	xor	ecx, ecx
	add	rax, 15
	and	rax, -16
	lea	rsi, [rsi+rax]
	cmp	QWORD PTR 16[rdi], rsi
	jb	.L13
	sub	rsp, 8
	mov	QWORD PTR 8[rdi], rsi
	add	rax, QWORD PTR [rdi]
	xor	esi, esi
	mov	rdi, rax
	call	memset@PLT
	add	rsp, 8
	ret
	.p2align 4,,10
	.p2align 3
.L10:
	xor	ecx, ecx
.L13:
	mov	rax, rcx
	ret
	.size	key_material_arena_alloc, .-key_material_arena_alloc
	.p2align 4
	.globl	key_material_arena_reset
	.type	key_material_arena_reset, @function
key_material_arena_reset:
	endbr64
	push	rbx
	mov	rbx, rdi
	mov	rdi, QWORD PTR [rdi]
	xor	esi, esi
	mov	rdx, QWORD PTR 8[rbx]
	call	memset@PLT
	add	QWORD PTR 24[rbx], 1
	mov	QWORD PTR 8[rbx], 0
	pop	rbx
	ret
	.size	key_material_arena_reset, .-key_material_arena_reset
	.p2align 4
	.globl	key_material_arena_destroy
	.type	key_material_arena_destroy, @function
key_material_arena_destroy:
	endbr64
	test	rdi, rdi
	je	.L18
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L18:
	ret
	.size	key_material_arena_destroy, .-key_material_arena_destroy
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	sub	rsp, 8
	mov	esi, 544
	mov	edi, 16
	call	aligned_alloc@PLT
	test	rax, rax
	je	.L20
	lea	rdx, 47[rax]
	movdqa	xmm0, XMMWORD PTR .LC0[rip]
	mov	QWORD PTR 24[rax], 0
	and	rdx, -16
	mov	QWORD PTR [rax], rdx
	movups	XMMWORD PTR 8[rax], xmm0
.L20:
	add	rsp, 8
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_alloc
	.type	_force_alloc, @function
_force_alloc:
	endbr64
	lea	rax, -1[rsi]
	cmp	rax, 495
	ja	.L28
	mov	rax, QWORD PTR 8[rdi]
	mov	rdx, rsi
	xor	ecx, ecx
	add	rax, 15
	and	rax, -16
	lea	rsi, [rsi+rax]
	cmp	QWORD PTR 16[rdi], rsi
	jb	.L31
	sub	rsp, 8
	mov	QWORD PTR 8[rdi], rsi
	add	rax, QWORD PTR [rdi]
	xor	esi, esi
	mov	rdi, rax
	call	memset@PLT
	add	rsp, 8
	ret
	.p2align 4,,10
	.p2align 3
.L28:
	xor	ecx, ecx
.L31:
	mov	rax, rcx
	ret
	.size	_force_alloc, .-_force_alloc
	.p2align 4
	.globl	_force_reset
	.type	_force_reset, @function
_force_reset:
	endbr64
	push	rbx
	mov	rbx, rdi
	mov	rdi, QWORD PTR [rdi]
	xor	esi, esi
	mov	rdx, QWORD PTR 8[rbx]
	call	memset@PLT
	add	QWORD PTR 24[rbx], 1
	mov	QWORD PTR 8[rbx], 0
	pop	rbx
	ret
	.size	_force_reset, .-_force_reset
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L36
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L36:
	ret
	.size	_force_destroy, .-_force_destroy
	.section	.rodata.cst16,"aM",@progbits,16
	.align 16
.LC0:
	.quad	0
	.quad	496
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
